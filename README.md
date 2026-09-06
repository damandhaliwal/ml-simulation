# Marketplace ETA Intelligence System

Current stage: **synthetic data, chronological evaluation, three baselines,
and a locally saved, full-data LightGBM ETA model with validated Python/CLI and
local HTTP prediction interfaces**. Training is offline; the API defaults to
localhost. Local Linux ARM64 Docker serving has passed user-run smoke checks;
cloud deployment is deferred under a hard **$0 budget**. The goal is to demonstrate
production-grade ML engineering locally, not claim a live production deployment.
See the handoff for the evidence and limitations.
There are no queues, courier dispatch, event clocks, intermediate stages, or
marketplace state. All data and measured model errors are simulated.

For the latest decisions, verified results, and next step, see
[the handoff](docs/handoff.md) and [session log](docs/session-log.md).

Next design step: [prediction/outcome logging contract](docs/prediction-logging.md).
The contract is accepted; persistence, database services, and replay are not
implemented. It separates immutable predictions from delayed outcomes and records
the accepted retry/durability behavior plus unresolved cancellation timing.

## Generator: two functions

Both live in `code/simulator/generate_orders.py`:

- `generate_order(confirmed_at, ...)` samples one complete row.
- `generate_orders(start_date, end_date, ...)` repeatedly calls it for a date range.

Features are sampled first. A short formula generates delivery duration from
order size, backlog, courier availability, distance, traffic, weather, a random
nearby-batch detour, and noise. All assumptions are synthetic, not estimates of
Toronto operations. See the [schema and formula](docs/order-schema.md).

## Function calls

From the repository root, start Python with `PYTHONPATH=code python3`:

```python
from datetime import date, datetime, timezone
from simulator.generate_orders import generate_order, generate_orders

# Dates belong in the call, not in the function's defaults.
orders = generate_orders(
    date(2026, 1, 1), date(2026, 8, 31),
    seed=42,
    orders_per_hour=20,
    couriers_per_zone=3,
    traffic_index=1.8,
    weather_type="rain",
)

# The same sampler can later supply individual orders for live testing.
order = generate_order(
    datetime.now(timezone.utc),
    order_id="LIVE-001",
    seed=42,
    traffic_index=2,
    weather_type="snow",
)
```

There is no market object to create or advance. Each row already contains its
synthetic outcome. For future live testing, send only inputs to the prediction
API and withhold the outcome until its timestamp; that replay logic is not built.

Useful keyword arguments:

| Controls | Defaults / meaning |
| --- | --- |
| `orders_per_hour`, `couriers_per_zone` | 20 arrivals/hour; 3 sampled local couriers. Rate also scales sampled workload. Zero local couriers is allowed. |
| `traffic_index`, `weather_type`, `temperature_c`, `precipitation_mm_per_hour` | Sample/derive defaults unless overridden. Supply compatible weather when setting temperature or precipitation. |
| `holidays`, `special_events` | Date-to-name dictionaries, empty by default; no official holiday calendar is bundled. |
| `holiday_name`, `special_event` | Override the label for a row. Labels alone do not increase traffic or demand. |
| `item_count`, `prep_time_multiplier` | Sample 1–5 items; preparation multiplier defaults to 1. |
| `promise_minutes`, `cancellation_probability` | A 45-minute promise; a 3% direct cancellation draw. |
| `batch_probability`, `max_orders_per_run`, `batch_max_gap_km` | A 20% chance of extra batch delay; at most 2 orders; pickup and drop-off gaps each at most 1 km. No linked runs are constructed. |
| `zones`, `restaurants_per_zone` | Three synthetic zones; 5 restaurants per zone. |

Extra keyword arguments to `generate_orders` are passed to the single-row
sampler. A call's scenario applies throughout that batch. To switch scenarios,
make new calls with different arguments.

## Run and check

The generator alone uses Python 3.10+ and the system's IANA timezone database.
Modeling uses the pinned environment in `requirements.txt` (Python 3.12+;
verified on CPython 3.13.7, macOS arm64). From a fresh checkout:

```sh
uv venv --python 3.13.7 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

This installs NumPy, scikit-learn, LightGBM, the FastAPI/Uvicorn server, the HTTPX2
test client, and their pinned dependencies into the project environment. The
model package versions are unchanged by the API step. LightGBM also
needs an OpenMP runtime on macOS (`libomp`, already present on the tested host).
If import fails with a missing `libomp.dylib`, install that system dependency
separately; the Python requirements cannot install it. Other platforms are untested.

```sh
.venv/bin/python code/simulator/generate_orders.py \
  --start 2026-01-01 --end 2026-08-31 --seed 42 --orders-per-hour 20 \
  --output data/orders_2026_jan_aug.json
PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -v
PYTHONPATH=code .venv/bin/python -m prep.dataset_validation
PYTHONPATH=code .venv/bin/python -m models.baselines
PYTHONPATH=code .venv/bin/python -m models.lightgbm_eta --segments
```

The CLI requires dates and an output path, and replaces that specific output
file if it exists. It also accepts `--seed` and `--orders-per-hour`.
Generated files are ignored by Git.

Dates are inclusive in Toronto-local time; timestamps are stored in UTC.
The window selects **confirmation dates**, not an outcome observation cutoff:
late-window orders can have delivery timestamps beyond the window.

The same seed, order ID, timestamp, and controls reproduce the same row.
Batch IDs are unique within a batch, not across separate batches; pass your own
unique `order_id` for individual calls. Independent calls carry no prior state.

The current local dataset is `data/orders_2026_jan_aug.json`: **116,667 orders,
31 columns**, covering all 243 days from January 1 through August 31, 2026.
It contains 113,079 delivered and 3,588 cancelled orders. The source file is
unchanged; preparation creates eligible train/validation/test splits in memory.
The command above reproduces this default-seed dataset. Holiday/event calendars
were not supplied, so holiday flags are false and holiday/event names are missing.
The three older dataset/sample files were moved to Trash; only this dataset
remains in `data/`. The data itself is not committed to Git.

## Prediction contract

- Predict at order confirmation.
- Target: `delivery_duration_minutes`.
- Do not use `status`, `delivered_at`, `delivery_duration_minutes`, or `late_delivery`
  as inference features. Cancelled rows have missing delivery labels.
- IDs are identifiers, not automatically model features.
- The promise is separate from the prediction. Lateness means delivery after
  that original deadline; a point prediction is not a lateness probability.
- MAE is the primary metric. Bias is predicted minus actual; P95 is the 95th
  percentile of absolute error. RMSE is also reported. All error units are minutes.

## Chronological evaluation

One row is one order. Date ranges refer to **Toronto-local confirmation dates**:

| Split | Confirmation dates (2026) | Label rule | Eligible delivered rows |
| --- | --- | --- | ---: |
| Train | January 1–June 30 | Delivered strictly before July 1 at 00:00 | 84,104 |
| Validation | July 1–31 | Delivered strictly before August 1 at 00:00 | 14,466 |
| Test | August 1–31 | Wait for every delivery, including after month-end | 14,481 |

Sixteen training labels and twelve validation labels arrive too late for their
cutoffs. They are returned separately as `unavailable_labels`, not deleted from
the source, moved to a different split, or included in evaluation denominators.
The 113,079 delivered rows reconcile to 113,051 eligible rows plus 28 exclusions.
Cancelled rows remain separate and are never assigned a zero-minute target.
For custom ranges, cutoffs are the next split's start date at Toronto midnight.
Timestamps must include a timezone and agree with the delivery duration.

The baselines are global mean, a fixed preparation/travel heuristic, and linear
regression on seven numerical confirmation-time features. LightGBM uses those
features plus temperature, calendar hour/day, zones, and weather. Its fixed
settings are 300 maximum trees, learning rate 0.05, 31 leaves, minimum 20 samples
per leaf, seed 42, and L1 loss. Only July validation selects the tree count
(25-round early stopping); there is no test-set tuning or train-plus-validation
refit before test scoring. Model/feature definitions remain in `code/models/`.

By default both evaluation functions and CLIs score **train and validation only**.
Use this explicit opt-in only when the configuration is frozen:

```sh
PYTHONPATH=code .venv/bin/python -W error -m models.lightgbm_eta --include-test --segments
```

`models.baselines` accepts the same flags. These commands do not save a model.
The opt-in is a guard against accidental test scoring, not a one-use lock.
August was explicitly evaluated on September 3; do not tune against those results.

Validation/test results include counts and errors by weather, pickup zone,
local hour (00–23), distance (≤2 km, >2–4 km, >4 km), and idle-courier count.
Each dimension partitions the scored population; dimensions must not be summed
together. Only observed groups are emitted: absent groups are **not evaluated**,
not zero-error groups. July/August contain no snow, and idle-courier counts are
only 1 or 2 in this default dataset. There are no agreed business error thresholds.

## Full-data refit and local artifact

Daman approved the refit after reviewing [the evaluation record](docs/evaluation-2026-09-03.md).
The local model uses all **113,079 delivered rows**, including the 28 labels
previously excluded by the earlier monthly cutoffs. All 3,588 cancelled rows are
excluded. It fits exactly **160 trees**, selected by July validation; features and
other settings are unchanged. No validation set or early stopping is used here.

```sh
PYTHONPATH=code .venv/bin/python -W error -m models.refit_eta \
  --data data/orders_2026_jan_aug.json \
  --output-dir artifacts/eta_2026_jan_aug \
  --observed-at 2026-09-03T00:00:00Z
```

The observation time is explicit: every confirmation and delivered outcome must
precede it. This command refuses existing output directories; choose a new name
for a new approved run. It reuses the raw-data validation but not the earlier
split filtering, so the full-data refit does not accidentally omit the 28 labels.

Two local, Git-ignored files are saved:

- `artifacts/eta_2026_jan_aug/model.joblib`: the fitted wrapper and LightGBM model.
- `artifacts/eta_2026_jan_aug/metadata.json`: source/model/code checksums, row counts,
  source and training windows, observation/training times, feature order and
  categorical mappings, prediction rounding/floor, model parameters, versions,
  and the number of rows checked for exact reload prediction equality.

After fitting, predictions are compared exactly before and after loading on all
113,079 training rows. The metadata sidecar is written only after this check.
A failed save can leave an incomplete directory; it is not a successful artifact.

For low-level inspection, load our own trusted artifact from Python started with
`PYTHONPATH=code` (use the validated interface below for incoming requests):

```python
from models.refit_eta import load_artifact

model, metadata = load_artifact("artifacts/eta_2026_jan_aug")
predictions = model.predict([order])  # Existing order dictionary; outcome fields are not required.
```

Joblib loading can execute code: **never load an untrusted artifact**, even if
its checksum matches a supplied metadata file. The loader checks format, feature
contract, exact Python/package versions, model checksum, fitted state, and tree
count. Checksums detect accidental corruption; they are not signatures. Reuse the
pinned environment. The low-level model's unchanged fallback maps unknown zones
or weather to -1; the new request interface rejects them before prediction.

January–August is now training data. The recorded August results describe the
earlier model, **not this refitted model**. No new held-out score is claimed; a
later untouched window is needed to assess the refit. This is still synthetic
learning work, not evidence of real-world delivery accuracy.

## Local prediction interface

The Python/CLI interface accepts **one JSON object**, validates it, loads the
explicitly selected trusted artifact, and returns one duration prediction. It does not
retrain, save predictions, start a server, or alter the artifact. Each call loads
the model again; this small local interface is not a latency-optimized service.

From the repository root, try the synthetic [example request](docs/prediction-request.example.json):

```sh
PYTHONPATH=code .venv/bin/python -W error -m models.predict_eta \
  --model-dir artifacts/eta_2026_jan_aug \
  --input docs/prediction-request.example.json
```

The current artifact returns:

```json
{
  "order_id": "EXAMPLE-001",
  "predicted_delivery_duration_minutes": 43.63,
  "model_sha256": "29447c8ee3ac6ac62d0f72b61d43f24668d01ed62b7974266b9f7991d3ca5dcd",
  "simulated": true
}
```

This is a synthetic example prediction, not a measured delivery or an accuracy
result. Duration is measured from confirmation; the existing 5-minute floor and
2-decimal rounding are unchanged. The SHA-256 identifies the exact model binary,
not the whole interface implementation or a trusted publisher.

The request contains exactly the 13 fields shown in the example:

- `order_id`: nonblank string, echoed back but not used as a model feature.
- `confirmed_at`: timezone-aware ISO timestamp. The interface derives
  `local_hour` and `day_of_week` in Toronto time, including daylight-saving changes.
  Do not supply those two derived features yourself.
- `distance_km` and `traffic_index`: positive, finite numbers.
- `item_count`: integer >= 1. `restaurant_backlog`, `orders_waiting_for_courier`,
  and `idle_couriers`: integers >= 0. JSON `2.0` is not an integer count here.
- `temperature_c`: finite number. `precipitation_mm_per_hour`: finite and >= 0.
- `pickup_zone_id` and `dropoff_zone_id`: `Z1`, `Z2`, or `Z3`.
- `weather_type`: `clear`, `rain`, `snow`, or `storm`. Match the simulator's toy
  rules: clear requires zero precipitation; the others require positive
  precipitation. Snow requires temperature <= 0; rain/storm require > 0.

There is no numeric string/boolean coercion, missing-value imputation, or clipping
of request values. Missing and extra fields are errors, including outcomes,
promised deadlines, and unused context fields from a full simulator row. To
construct a request from a generated row, explicitly select `REQUEST_FIELDS`.
The promise remains separate; no lateness probability or cancellation forecast
is returned. A valid request is not proof of training-distribution coverage:
no empirical upper-bound or out-of-distribution gate is implemented yet.

The same boundary is available from Python:

```python
import json
from pathlib import Path
from models.predict_eta import predict_eta

request = json.loads(Path("docs/prediction-request.example.json").read_text())
result = predict_eta(request, "artifacts/eta_2026_jan_aug")
```

`validate_request(request)` returns the 13 model features without changing its
input. `predict_eta(request, artifact_dir)` validates before loading and returns
the response dictionary. The CLI prints JSON only on success; malformed request
JSON, request validation failures, and ordinary file/compatibility errors exit
with status 2 and an error on stderr. It never silently falls back to another model.

## Local HTTP API

`code/serving/api.py` exposes the same request contract through FastAPI. It calls
the existing validator and feature extraction; no new feature definitions or
model fitting are introduced. The saved artifact is loaded **once per server
startup**, not once per request, using FastAPI's
[lifespan mechanism](https://fastapi.tiangolo.com/advanced/events/).

Start it from the repository root after installing `requirements.txt`:

```sh
PYTHONPATH=code .venv/bin/python -W error -m serving.api \
  --model-dir artifacts/eta_2026_jan_aug --port 8000
```

This command runs in the foreground on **127.0.0.1**, with one worker process.
The optional `--host` argument changes the listening address; the default remains
`127.0.0.1`. Use `--host 0.0.0.0` inside a container when configuring Docker port
forwarding, not for ordinary local runs of this unauthenticated service. Publish
the container port on the host's `127.0.0.1` to keep host access local.
Stop it with **Ctrl+C**. From another terminal:

```sh
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/predict \
  -H 'Content-Type: application/json' \
  --data-binary @docs/prediction-request.example.json
```

- `GET /health`: HTTP 200 with `status: "ready"`, `model_sha256`, and `simulated`.
  This means the checked artifact is loaded, not that its accuracy is acceptable.
- `POST /predict`: HTTP 200 with the same response as the CLI. The existing example
  returns 43.63 minutes with the same model checksum. It is still synthetic.
- Invalid/missing fields or malformed/non-object JSON: HTTP 422 with a `detail`
  string and no prediction. Numeric strings/booleans, unknown categories, and
  outcome fields remain invalid. Arbitrary invalid bodies are not echoed back.
- Missing, corrupt, or incompatible artifact: startup fails. There is no fallback
  model. An app called outside its startup lifecycle reports HTTP 503, not ready.
- Unexpected inference errors remain server errors (HTTP 500), not misleading
  input-validation errors. Wrong methods return 405; unknown routes return 404.

Optional prediction logging: send `X-Run-Id` and `X-Predicted-At`
(timezone-aware ISO timestamp) together. The run must already be registered
(see `code/persistence/predictions.py:insert_run`); the prediction row is
committed before the 200 response, and the response is built from the stored
row, so an identical retry returns byte-identical JSON with one stored row.
A different payload/model/value under the same key returns 409; an unknown
run returns 422; half-supplied or naive-timestamp headers return 422. Logging
needs `POSTGRES_DB`, `POSTGRES_APP_USER`, and `POSTGRES_APP_PASSWORD` in the
environment (`PGHOST` defaults to 127.0.0.1, `PGPORT` to 5432); a configured
but unreachable store fails startup, and run headers with no database return
503. Requests without the headers predict exactly as before and store nothing.
The API connects as the least-privileged `eta_app` login, never the admin.

`create_app(artifact_dir)` builds the app without loading anything. Its startup
context loads and retains the model/metadata, and clears the reference on shutdown.
The two routes use that in-memory snapshot. If model files change on disk, restart
with the explicitly selected artifact to load a new version; there is no hot swap.
The synchronous prediction route runs off the async event loop. One worker is
not a throughput guarantee; load/concurrency testing has not been done.

The HTTP client is **HTTPX2** because the pinned Starlette test client deprecates
HTTPX. AnyIO is pinned to 4.14.2 because Starlette 1.6.0 imports aliases deprecated
in 4.15; no warning suppression is used. All 95 tests pass with `-W error` when
the local database is configured; without it 82 run with 14 clean skips (12
persistence tests plus the API-logging and replay classes). Both modes hold in the
project environment and in a fresh environment installed from `requirements.txt`. See
[Starlette's test-client documentation](https://www.starlette.io/testclient/).

This is a localhost learning service, **not a public deployment**: no authentication,
TLS, rate limiting, outcome ingestion, or delayed-outcome processing yet. Prediction
rows are written through the least-privileged login above; Uvicorn's ordinary
request/error logs are not model-monitoring records. Cloud deployment still
requires explicit approval.

## Local Docker API

`Dockerfile` packages Python 3.13.7, the pinned requirements, and the API's Python
modules. It installs `libgomp1` for OpenMP and `tzdata` for Toronto timezone data,
then runs as UID/GID `10001:10001`. The Python patch version matches the saved
artifact's strict runtime checks; this is a local compatibility baseline, not
a claim of production security hardening.

`.dockerignore` excludes everything except the packaging files, requirements,
and Python files directly inside `code/models`, `code/prep`, `code/serving`,
and `code/persistence`.
Only requirements and those Python files are copied into the image. Agent
instructions, docs, simulator, tests, caches, Git history, environment files, datasets, and
model artifacts are excluded. Do not add directory-only exceptions such as
`!code/`: Docker also applies those matches to descendants, widening the allowlist.

The saved model is deliberately **not inside the image**. Before starting it,
you need our own trusted `model.joblib` and `metadata.json` in the local artifact
directory described above. A Git clone alone does not supply the model. Joblib
can execute code while loading; never substitute an untrusted artifact.

From the repository root, with Docker running:

```sh
docker build --progress=plain -t eta-api:local .
docker run --rm --name eta-api \
  -p 127.0.0.1:8000:8000 \
  --mount type=bind,source=./artifacts/eta_2026_jan_aug,target=/model,readonly \
  eta-api:local
```

The relative mount source assumes this working directory; an absolute artifact
path also works (quote the full mount argument when that path contains spaces).
The model directory is read-only, not the whole repository. These commands build
and run locally; they do not retrain or upload the image.

The default exec-form command runs `python -m serving.api --model-dir /model
--host 0.0.0.0 --port 8000`. That address is inside the container. The `-p` option
separately publishes it on the host's localhost only. `EXPOSE 8000` is descriptive
metadata, not a port-publishing rule. Do not replace the mapping with `-p 8000:8000`
for this unauthenticated local service. No `--network none` here: HTTP access needs
the container network. See [port publishing](https://docs.docker.com/engine/network/port-publishing/)
and [read-only bind mounts](https://docs.docker.com/engine/storage/bind-mounts/).

After startup completes, use a second terminal at the repository root:

```sh
curl --include --fail-with-body --max-time 10 http://127.0.0.1:8000/health
curl --include --fail-with-body --max-time 10 http://127.0.0.1:8000/predict \
  -H 'Content-Type: application/json' \
  --data-binary @docs/prediction-request.example.json
curl --include --max-time 10 http://127.0.0.1:8000/predict \
  -H 'Content-Type: application/json' --data-binary '{}'
curl --include --max-time 10 http://127.0.0.1:8000/predict \
  -H 'Content-Type: application/json' --data-binary '{'
```

Expected results: health 200/ready; prediction 200 at **43.63 minutes** for
`EXAMPLE-001`, with the recorded model checksum and `simulated: true`; incomplete
and malformed requests return 422 without a prediction. These match Daman's
pasted container HTTP results. This is a single-example portability smoke check,
not a fresh held-out model evaluation or comprehensive cross-platform parity test.

Stop the foreground server with Ctrl+C, then check removal:

```sh
docker ps -a --filter 'name=^/eta-api$' --format '{{.Names}}'
```

Expected: no output. `--rm` removes the stopped container, not the image or host
model. A name in that listing means the container still exists; `-a` includes
stopped containers as well. Do not mistake an HTTP 200 for successful shutdown.

Optional image-only checks (these commands override its API startup command):

```sh
docker run --rm --network none eta-api:local find /app -type f
docker run --rm --network none eta-api:local python -m pip --no-cache-dir check
```

The application file listing should contain requirements plus twelve Python files,
with no simulator or caches. `--no-cache-dir` avoids pip's unwritable-cache warning
for the numeric non-root user. These inspection commands need no model mount.

Scope: tested locally on Linux ARM64 through Docker Desktop on an Apple Silicon
Mac. The full Python suite was run on macOS, not inside the image. AMD64, full
Linux-suite execution, runtime/test dependency separation, vulnerability scanning,
load testing, authentication/TLS, image-registry publication, and cloud deployment
remain unverified or deferred. The API image smoke test does not require the local
Compose/PostgreSQL service. Detailed evidence is in [the handoff](docs/handoff.md).

## Local PostgreSQL

`compose.yaml` runs the pinned official PostgreSQL 17.11 Bookworm image with a
named volume, health check, and host access restricted to `127.0.0.1:5432`. It
holds the `app` schema (`runs`, `predictions`, `outcomes`, all owned by
`eta_admin`) behind migration `db/migrations/001_app_logging.sql. The API
connects only as the least-privileged `eta_app` login (`INSERT`/`SELECT`).

Create the ignored local environment file and replace the password placeholder
with a real local administrator secret:

```sh
cp .env.example .env
chmod 600 .env
```

The host-side `POSTGRES_ADMIN_*` names make the privilege boundary explicit.
Compose maps them to the official image's required bootstrap variables. That
bootstrap identity is intentionally a superuser; it must never be used by the API.
`eta_app` has a different secret and restricted privileges (see
`docs/db-eta-app-design.md`).

Validate without printing the resolved configuration, then start the service:

```sh
docker compose config --quiet
docker compose config --images
docker compose up --detach --wait --wait-timeout 60 postgres
docker compose ps
```

Do not run plain `docker compose config`, which may print the resolved password.
Expected state: `eta-local-postgres-1` is healthy, the image matches the digest in
`compose.yaml`, and the host mapping is `127.0.0.1:5432->5432/tcp`.

Verify the initialized identity over TCP/password authentication without printing
the password:

```sh
docker compose exec postgres sh -eu -c '
  PGPASSWORD="$POSTGRES_PASSWORD" psql \
    --host 127.0.0.1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --no-psqlrc \
    --tuples-only \
    --no-align \
    --set ON_ERROR_STOP=1 \
    --command "SELECT current_database(), current_user, rolsuper, version() FROM pg_roles WHERE rolname = current_user;"
'
```

Expected output begins `eta|eta_admin|t|PostgreSQL 17.11`; `t` is intentional for
this administrator. Stop the service while retaining its data with:

```sh
docker compose down
```

`docker compose down --volumes` also deletes `eta_postgres_data` and is destructive.
Use it only for an explicitly reviewed reset. Neither command removes the image.

## Live replay

`code/replay/harness.py` replays a source window through the running API in
confirmation order, then joins stored predictions to stored outcomes. Start the
API and database first, then from the repository root with DB credentials set:

```sh
PYTHONPATH=code .venv/bin/python -m replay.harness \
  --source data/orders_2026_jan_aug.json --run-id REPLAY-EXAMPLE \
  --start 2026-08-04 --end 2026-08-04 --model-dir artifacts/eta_2026_jan_aug
```

Each order is POSTed with run headers (`predicted_at` equals its confirmation
time); outcomes are ingested as simulated cutoffs pass them, with a final sweep
for slow deliveries. The printed report gives matched/pending/cancelled counts
plus MAE, bias, and P95 over matched delivered pairs. Rerunning the same run ID
resumes idempotently. Replaying training-window data exercises the plumbing
only; it is not a held-out evaluation. Cancelled orders are counted, never
ingested — their observation timing is still undecided.

The broader roadmap and working agreement remain in [AGENTS.md](AGENTS.md).
