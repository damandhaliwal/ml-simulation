# `eta_app` role and first migration — design only

Status: design accepted for review, not implemented. No role, schema,
table, grant, driver, or API change exists yet. See `AGENTS.md` next step,
`docs/prediction-logging.md` for the logging contract, and `docs/handoff.md`
for PostgreSQL bootstrap evidence.

## Objective

Give the future prediction logger a distinct login that can append and
re-read its own rows, and nothing else. The API must never receive the
`eta_admin` credential. `eta_admin` remains the only superuser and the only
owner of schema and tables.

Current state: database `eta`, bootstrap administrator `eta_admin`
(`rolsuper = true` by official-image design), zero user tables, healthy local
service on `127.0.0.1:5432`, pinned image
`postgres:17.11-bookworm@sha256:051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0`.
`.env` stays ignored with mode `600`; never print it, read it back, or print
resolved Compose config.

## 1. Role

```sql
CREATE ROLE eta_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  CONNECTION LIMIT 5 PASSWORD '<distinct-local-password>';
```

* `LOGIN`: can connect; `NOSUPERUSER/NOCREATEDB/NOCREATEROLE`: cannot
  become admin, create databases, or create other roles.
* Password is a different local secret from `POSTGRES_ADMIN_PASSWORD`.
  Daman supplies it in `.env`; only a placeholder ever enters Git.
* No `BYPASSRLS`, no replication flag, no expiry change in this step.

### Bootstrap mechanism

Daman runs the role statement by hand as `eta_admin` via
`docker compose exec postgres psql`, then runs migration `001` in the same
session. Example session shape (password omitted):

```sh
docker compose exec postgres psql -U eta_admin -d eta -f db/migrations/001_app_logging.sql
```

Why manual, not `docker-entrypoint-initdb.d`: bootstrap scripts run only on
an empty data directory. Our named volume `eta_postgres_data` already exists,
so an init script would silently do nothing. Manual application is explicit,
pasted for review, and matches the learning-first workflow.

Future `.env` additions (not in this design step):

```sh
POSTGRES_APP_USER=eta_app
POSTGRES_APP_PASSWORD=replace-with-a-different-local-password
```

`.env.example` will carry placeholders only. Compose needs no new service;
the app connects to the existing `postgres` service on `127.0.0.1:5432`.

## 2. Migration format

One plain SQL file, reviewed before it runs:

```text
db/migrations/001_app_logging.sql
```

* Plain PostgreSQL, no Alembic/SQLAlchemy/psycopg dependency in this step.
* Applied once by hand with `psql -f`; its SHA-256 is recorded in the handoff.
* Later migrations add `002_...sql`, never edit `001` after it is applied.
* The file must be idempotent-safe by inspection only (`CREATE SCHEMA IF NOT
  EXISTS` plus guarded creates); rerunning against an applied database is an
  error to investigate, not a silent no-op.

## 3. Schema and ownership

```sql
CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION eta_admin;
```

* New `app` schema, owned by `eta_admin`. Do not use `public`.
* All three tables below are owned by `eta_admin` (`AUTHORIZATION eta_admin`
  or `ALTER TABLE ... OWNER TO eta_admin` after creation).
* `eta_app` owns nothing, so a compromised API process cannot `ALTER` or
  `DROP` its own tables.

## 4. Tables (migration 001)

Types mirror `docs/prediction-logging.md`. Timestamps are `TIMESTAMPTZ`
normalized to UTC. Money/promise math stays in Python; the database stores
facts and enforces uniqueness, not model logic.

`app.runs`: one row per replay experiment.

```sql
CREATE TABLE app.runs (
  run_id TEXT PRIMARY KEY,
  schema_version INT NOT NULL DEFAULT 1,
  simulated BOOL NOT NULL DEFAULT TRUE CHECK (simulated),
  source_sha256 TEXT NOT NULL,
  source_order_count INT NOT NULL CHECK (source_order_count > 0),
  scenario JSONB NOT NULL,
  code_commit TEXT NOT NULL,
  image_id TEXT NOT NULL,
  model_sha256 TEXT NOT NULL,
  model_metadata_sha256 TEXT NOT NULL,
  created_at_wall TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`app.predictions`: one successful logical prediction per
`(run_id, order_id)`. `request_payload` uses the 13 `REQUEST_FIELDS` from
`code/models/predict_eta.py`; `features` is the dict returned by
`validate_request`, including derived `local_hour`/`day_of_week`.

```sql
CREATE TABLE app.predictions (
  run_id TEXT NOT NULL REFERENCES app.runs (run_id),
  order_id TEXT NOT NULL,
  request_payload JSONB NOT NULL,
  features JSONB NOT NULL,
  predicted_delivery_duration_minutes DOUBLE PRECISION NOT NULL CHECK (predicted_delivery_duration_minutes >= 5.0),
  model_sha256 TEXT NOT NULL,
  predicted_at_simulated TIMESTAMPTZ NOT NULL,
  recorded_at_wall TIMESTAMPTZ NOT NULL DEFAULT now(),
  model_latency_ms DOUBLE PRECISION NOT NULL CHECK (model_latency_ms >= 0),
  simulated BOOL NOT NULL DEFAULT TRUE CHECK (simulated),
  PRIMARY KEY (run_id, order_id)
);
```

`app.outcomes`: at most one terminal outcome per `(run_id, order_id)`,
written by a separate future ingestion path. Cancellation timing is still
unresolved: no `cancelled_at` column is added here, and no cancellation time
is invented.

```sql
CREATE TABLE app.outcomes (
  run_id TEXT NOT NULL REFERENCES app.runs (run_id),
  order_id TEXT NOT NULL,
  confirmed_at TIMESTAMPTZ NOT NULL,
  promised_delivery_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('delivered', 'cancelled')),
  delivered_at TIMESTAMPTZ,
  delivery_duration_minutes DOUBLE PRECISION,
  late_delivery BOOL,
  outcome_available_at_simulated TIMESTAMPTZ NOT NULL,
  observed_at_simulated TIMESTAMPTZ NOT NULL,
  recorded_at_wall TIMESTAMPTZ NOT NULL DEFAULT now(),
  simulated BOOL NOT NULL DEFAULT TRUE CHECK (simulated),
  PRIMARY KEY (run_id, order_id),
  CHECK (observed_at_simulated > outcome_available_at_simulated),
  CHECK (
    (status = 'delivered' AND delivered_at IS NOT NULL
      AND delivery_duration_minutes IS NOT NULL AND delivery_duration_minutes > 0
      AND late_delivery IS NOT NULL)
    OR (status = 'cancelled' AND delivered_at IS NULL
      AND delivery_duration_minutes IS NULL AND late_delivery IS NULL)
  )
);
```

Time rules from the logging contract stay in application code, restated here
for reviewers: admit an outcome only when `outcome_available_at_simulated`
is strictly less than `observed_at_simulated`; for an as-of snapshot at `T`,
also require `observed_at_simulated <= T`. The `CHECK` above enforces the
first inequality; snapshot filtering remains a query predicate, not a
constraint. Re-ingesting identical labels is `ON CONFLICT DO NOTHING`;
conflicting labels are an application-level error, never a silent overwrite.
Operational attempt logs from section 2 of the logging contract are deferred;
this migration creates no attempt table.

## 5. Exact grants

Run as `eta_admin` after the tables exist:

```sql
GRANT CONNECT ON DATABASE eta TO eta_app;
GRANT USAGE ON SCHEMA app TO eta_app;
GRANT INSERT, SELECT ON app.runs TO eta_app;
GRANT INSERT, SELECT ON app.predictions TO eta_app;
GRANT INSERT, SELECT ON app.outcomes TO eta_app;
```

What is deliberately absent: no `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`
grant beyond the foreign keys above, no `CREATE`/`USAGE` on other schemas, no
`ALTER/DROP` (ownership stays with `eta_admin`), no new
`ALTER DEFAULT PRIVILEGES`. A future table needs a new explicit `GRANT` in a
new migration; permissions never expand silently.

Why `INSERT + SELECT` but no `UPDATE`: first-write-wins retries are
`INSERT ... ON CONFLICT DO NOTHING` followed by `SELECT` of the committed
row. Failed writes return a service error, never an unlogged success.

## 6. Focused checks (run after implementation is approved)

Positive (as `eta_app`): `INSERT` one `runs` row, one `predictions` row, one
`outcomes` row; `SELECT` each back; duplicate `INSERT` with identical payload
does nothing and `SELECT` returns the original timestamps.

Negative (as `eta_app`, each must fail): `CREATE TABLE`, `DROP TABLE`,
`DELETE`, `UPDATE`, `ALTER TABLE`, `SELECT` from `pg_authid`, connecting with
a wrong password. Catalog re-check as `eta_admin` confirms `eta_app` has
`rolsuper = false`, owns zero objects, and `app` tables are owned by
`eta_admin`.

Timeout reconciliation, restart durability, concurrent duplicates, and the
six acceptance groups in `docs/prediction-logging.md` remain future API
integration tests, not part of this role/migration step.

## Out of scope

No `CREATE ROLE`, no migration run, no `psycopg` dependency, no API logger,
no replay harness, no operational attempt table, no cancellation-policy
change, no cloud resource, no public port. The next implementation step, if
approved, adds the role, applies `001`, runs the checks above, and records
hashes and pasted evidence in `docs/handoff.md`.
