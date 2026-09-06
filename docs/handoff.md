# Handoff — September 4, 2026

## September 6, 2026 — first held-out score (September week, seed 7)

Fresh September 1–7 data, replayed live under `EVAL-SEPW1-SEED7`: 3,407 posted,
3,311 matched, 96 cancelled counted, MAE 3.320 / bias -0.568 / P95 9.120.
Storm stays weakest (MAE 3.789, bias -1.261). Rerun byte-identical; metrics
independently recomputed; artifact hash unchanged; rows admin-deleted after.
Full record in `docs/evaluation-2026-09-06.md`. No tuning against these scores.
Decided: cancellations stay coverage-only (see `docs/prediction-logging.md`);
delivered-only terminal replay is the closed policy, not a gap.

## September 6, 2026 — live replay through the API

`code/replay/harness.py` posts every order in confirmation order with run
headers, ingests delivered outcomes as cutoffs pass them, and scores matched
pairs with the shared metric helper. Cancellations are counted, never ingested.
First live run: August 4 slice, 480 orders → 480 predictions, 470 matched,
10 cancelled, 0 pending, MAE 2.978 / bias -0.503 / P95 7.650. That window is
training data, so this is a plumbing check, not a held-out result; the slice
run was admin-deleted afterwards. 4 replay tests plus 95 total pass with
`-W error` (82 run / 14 skip without DB creds); residue `0|0|0`.

## September 6, 2026 — outcome ingestion module (delivered-only)

`code/persistence/outcomes.py` stores terminal delivery outcomes: identical
re-ingest keeps first observation/recording times, conflicting labels raise,
impossible endings (bad timestamps, duration mismatch, wrong late flag,
unavailable-at-observation) raise, and cancelled rows are refused until a
cancellation observation policy is agreed. No endpoint or replay yet.
12 persistence tests and 91 total pass with `-W error` against the local
stack (82 run / 13 skip without DB creds); residue `0|0|0`.

## September 6, 2026 — prediction logging wired into /predict

`/predict` now commits its prediction row before responding 200 when the
caller sends `X-Run-Id` + `X-Predicted-At` together; responses are built from
the stored row, so identical retries return identical JSON with one stored row.

- `code/persistence/db.py`: env-only config (`POSTGRES_DB/APP_USER/APP_PASSWORD`,
  optional `PGHOST`/`PGPORT`); absent trio means unlogged, partial trio is a
  startup `RuntimeError`, configured-but-unreachable store also fails startup.
  No secrets in code or logs. `PredictionConflict(ValueError)` marks
  same-key-different-content retries, mapped to HTTP 409; unknown run_id maps
  to 422 via the foreign key; store errors map to 503; unconfigured store with
  run headers maps to 503. Headerless requests behave exactly as before.
- `.dockerignore` gains `!code/persistence/*.py`; without it the image would
  miss the new import. The image itself was not rebuilt here; that stays a
  separately approved Docker step.
- Tests: 85 pass with `-W error` against the local stack (76 run / 7 skip
  without DB creds). New `tests/test_api_logging.py` covers logged/unlogged,
  retry-identical, 409-conflict, half/naive headers, unknown run, 503 paths,
  and startup failure. Residue `0|0|0` across runs/predictions/outcomes.
- Deliberate deviation: the API stays usable without a database (unlogged),
  instead of refusing all startup — banning unlogged local predictions would
  have forced the whole suite onto the database. Fail-fast applies where the
  durability promise lives: configured-but-unreachable fails at startup, and
  run headers without a store fail per request with 503.
- Next: outcome ingestion, then replay through the API; cancellation
  observation policy still unresolved per `docs/prediction-logging.md`.

## September 5, 2026 — eta_app role and migration 001 applied

Daman created the least-privileged login and the first migration was applied;
no logger, driver, API integration, or replay exists yet.

- Role (Daman-created, verified): `eta_app|f|f|f|5` — login, non-superuser,
  no createdb/createrole, connection limit 5. Earlier pasted password was
  replaced with a new distinct local secret; only placeholders are in Git.
- Migration file `db/migrations/001_app_logging.sql` SHA-256:
  `8ee5e0af3c1121728e40692745c7e08e001a1c3f59b5a4e7b3ee01ffe023b977`.
  `.env.example`: `05ae49b2229ddf6a2e784da4c49fba8ca00392f0615c65e3640806258e2a5a5d`.
  `compose.yaml` unchanged: `5adc22d1f66826fc90a8ff4381f3ad9e54203451541b76225231b5a8b1176f91`.
- Apply as `eta_admin` via stdin with `ON_ERROR_STOP=1`: schema + 3 tables +
  ownership + 5 grants, `APPLY OK`. No `CREATE ROLE` inside the file.
- Ownership: schema `app` and tables `runs/predictions/outcomes` owned by
  `eta_admin`; `eta_app` owns 0 objects. Grants: `INSERT, SELECT` only on all
  three tables; schema `USAGE t / CREATE f`; database `CONNECT t`.
- Positive as `eta_app` (rolled back): 3 `INSERT`s + `SELECT` saw 1 row;
  after `ROLLBACK`, `app.predictions` holds 0 rows. No test residue.
- Negative as `eta_app`, all denied: `CREATE` (schema), `DELETE`/`UPDATE`
  (table), `DROP` (must be owner), `pg_authid` read.
- Daman's TCP/password check returned `eta_app` for `SELECT current_user;`.
  Socket checks above used container-local trust, no password handling.
- Service still healthy on `127.0.0.1:5432`; `app` holds 3 tables, 0 prediction
  rows. All 70 tests pass with `-W error` (4.216s). `git diff --check` passes.
- Next: API logger/persistence and outcome ingestion remain separate approved
  steps; cancellation observation policy still unresolved per
  `docs/prediction-logging.md`.

## Session topic

Completed a user-led local PostgreSQL startup/authentication/persistence exercise,
found that the original bootstrap `eta_app` was a superuser, and corrected the
configuration before publication. Daman reset the verified-empty volume and
recreated the database with the explicit bootstrap administrator `eta_admin`.
Codex independently verified the final non-secret configuration, runtime boundary,
and empty catalog. The next separate step is designing the least-privileged
application role and first schema migration; neither exists yet.

The goal remains production-grade ML skills with a hard $0 budget. Cloud Run was
discussed and deferred; no cloud resources or paid services were created. The
accepted [local prediction/outcome logging contract](prediction-logging.md) is
still a design: there is no logger, application schema, driver, or API integration.

The prior Docker closeout is published in `79d3ddf`, the API host option in
`c7c880b`, the original API in `8f1159f`, and Python/CLI interface in `6e8c0aa`.
The Docker evidence and earlier test timings below are historical; current
documentation-step checks are recorded separately here.
The earlier [evaluation record](evaluation-2026-09-03.md) remains unchanged and
describes the pre-refit model, not a held-out score for the new artifact.

## Key decisions

- Run PostgreSQL 17.11 Bookworm locally through Docker Compose with an immutable
  image digest, named volume, health check, and `127.0.0.1:5432` host binding.
- `.env` stays ignored/mode `600`; `.env.example` contains placeholders only.
  Never print resolved Compose configuration because it can expose the password.
- The persistence probe survived a reported container removal/recreation, then was
  removed. Codex independently confirmed no verification schema and zero user
  tables. PostgreSQL remains healthy and running with its volume retained.
- The official image makes `POSTGRES_USER` a superuser. Host-side configuration now
  names that identity `POSTGRES_ADMIN_USER=eta_admin` and maps it to the image's
  required variable. Live inspection confirms the intentional administrator has
  `rolsuper = true`. A later step creates a different least-privileged API role.
- Hard $0 budget; retain the operational ML roadmap locally. No paid service,
  billing enablement, or cloud provisioning based on anticipated free-tier usage.
- Logging is an accepted design, not implemented behavior. Its records are run
  context, one immutable prediction per `(run_id, order_id)`, and a separately
  observed terminal outcome. Run IDs avoid collisions across generator batches.
- Daman accepted first-write-wins retries and commit-before-success behavior.
  Distinguish model timing, HTTP attempts, wall-clock
  recording time, and simulated outcome availability.
- No cancellation timestamp exists in the source. Cancellation observation policy
  remains unresolved; never invent a time or infer cancellation from missing labels.
- One strict JSON request -> existing confirmation-time validator -> loaded
  model -> duration, order ID, model SHA-256, and synthetic-data label.
- `create_app(artifact_dir)` constructs the API without loading the model. FastAPI
  lifespan loads the trusted artifact once at startup and clears it on shutdown.
  Fail startup on missing/corrupt/incompatible artifacts; never choose a fallback.
- CLI defaults to `127.0.0.1`, one worker; explicit `--host` overrides the address.
  Use `0.0.0.0` only inside Docker for this local workflow, and publish the host
  port on `127.0.0.1`. HTTP 422 for invalid request bodies/domain
  values; 503 if called outside the loaded lifecycle; unexpected inference errors
  stay 500. Health means loaded/readiness, not model quality.
- Docker starts the API on container address `0.0.0.0:8000`, running as numeric
  UID/GID `10001:10001`. Publish the host port with `127.0.0.1:8000:8000` and mount
  the trusted artifact directory read-only at `/model`. The model is not baked in.
- Keep the corrected default-deny ignore rules: include only requirements,
  packaging files, and Python files directly in models/prep/serving; no broad
  directory exceptions. A clone alone lacks the ignored local model artifact.
- Added FastAPI/Uvicorn/HTTPX2 and pinned dependencies to `requirements.txt`.
  AnyIO 4.14.2 avoids Starlette 1.6.0's deprecated-alias warnings in 4.15.
- Derive Toronto-local hour/day from the timestamp; reject extra fields,
  outcomes, unknown categories, invalid numeric types/ranges, and contradictory
  synthetic weather. No imputation, clipping, or unknown-category fallback at
  this boundary. Existing training/model code remains unchanged.
- Daman explicitly approved the refit/save/load step. Use all 113,079 delivered
  labels, including the 28 excluded from earlier snapshot evaluation. Exclude
  3,588 cancellations. Observe labels strictly before September 3 at 00:00 UTC.
- Fit exactly 160 trees selected by July validation, with the existing features
  and remaining settings unchanged. No validation set or early stopping on refit.
- Save the wrapper/model using the already-installed Joblib package, plus a JSON
  sidecar containing the feature contract, settings, counts, windows, and hashes.
- Refuse existing output directories. Load only trusted artifacts and require
  compatible feature metadata, exact Python/package versions, checksum, and trees.
- No full-data retraining, application database integration, replay, cloud
  deployment, or remote infrastructure. The local PostgreSQL service is the only
  database infrastructure currently running.

## Current PostgreSQL evidence

- User-pasted: `.env` ignored; Compose validation and pinned image; first healthy
  startup; authenticated TCP result `eta|eta_app|PostgreSQL 17.11 ... aarch64`;
  transactional probe insert; probe-schema cleanup and final `t` absence check.
- User-reported without pasted output: after `docker compose down` (without
  `--volumes`), the container was absent and named volume remained; after restart,
  the probe row still read `1|survives restart`.
- Correction pasted by Daman: the verified-empty old volume was removed and a new
  volume/container were created; startup reached healthy in 5.6 seconds; TCP
  authentication returned `eta|eta_admin|t|PostgreSQL 17.11 ... aarch64`; the
  empty-catalog check returned `t|0`; and Docker inspection showed the pinned
  image, localhost-only port, and writable named volume.
- Final independently checked by Codex: quiet Compose validation; exact non-secret
  `.env.example`/Compose contents; file modes `600` for ignored `.env` and `644`
  for the publishable files; healthy container; writable `eta_postgres_data`;
  `127.0.0.1:5432`; and catalog result `eta|eta_admin|t|t|0` (database, user,
  intentional superuser, verification absent, zero user tables).
- Security source: the
  [official PostgreSQL image documentation](https://github.com/docker-library/docs/blob/master/postgres/README.md?plain=1)
  states that `POSTGRES_USER` creates a role with superuser power and bootstrap
  variables only apply to an empty data directory. This is why the correction used
  a reviewed volume reset instead of merely changing the running container config.
- `.env.example` and `compose.yaml` are included in this closeout. Unrelated
  `AGENTS.html` / `AGENTS_files/` remain untouched and untracked. `.env` is ignored.
  No database driver, application schema, API/model change, or new evaluation.

## Prior documentation-step checks

- All 70 existing tests pass with warnings treated as errors in the project
  environment: `PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -q`
  (4.157 seconds). No new persistence/replay tests exist yet.
- A focused in-memory check confirms the existing example has exactly 13 request
  fields, validation produces the 13 artifact-contract features, and Toronto
  hour/day derivation agrees. Neither request nor feature snapshot contains labels.
- A single in-memory cancellation fixture confirms null delivery labels and no
  `cancelled_at`; the illustrative -6.37-minute error was checked arithmetically.
  No new evaluation dataset, replay, or model-quality result was produced.
- Model and metadata checksums match the prior recorded artifacts. No runtime
  code, dependencies, packaging, data, or models changed. `git diff --check` passes.

## Prior Docker smoke evidence and closeout checks

- Daman built and ran the image. His pasted output confirms Linux ARM64,
  non-root configuration, compatible installed dependencies, and API imports with
  Python 3.13.7 and all eight recorded model-package versions.
- The initial file listing exposed Codex's incorrect `!code/` exception, which
  allowed simulator/cache descendants. The corrected rules have no directory-only
  exceptions. Daman reported the corrected file check passed; the corrected
  listing was not pasted or independently rerun by Codex.
- Daman pasted actual HTTP results from the rebuilt API image on September 3 EDT
  (September 4 UTC): health 200/ready at 01:25:34 UTC; `EXAMPLE-001` prediction 200
  at 43.63 minutes at 01:29:47 UTC; `{}` returns 422/missing fields at 01:29:57 UTC;
  malformed `{` returns 422/valid-JSON-object error at 01:30:11 UTC. Successful
  responses carry the exact saved model checksum below and `simulated: true`.
- This one prediction matches the previously recorded Mac Python/CLI/API result.
  Standalone Mac/Linux comparison commands were accepted earlier without pasted
  outputs. Do not claim comprehensive cross-platform prediction parity.
- The first post-test container listing still showed `eta-api`. After stopping
  the foreground API process, Daman reported no matching container. Codex then
  independently confirmed the empty filtered `docker ps -a` result read-only.
  Graceful-shutdown logs and the stopped container's exit code were not captured.
- Codex read the final image configuration: `linux/arm64`, UID/GID `10001:10001`,
  working directory `/app`, exposed port `8000/tcp`, and the expected exec-form
  API command with `/model`, `--host 0.0.0.0`, and `--port 8000`.
  Image ID: `sha256:a47342eac379289f72ac62be109f7337828a740df3d8b4d8cb2dea3f6da48443`;
  created `2026-09-04T01:25:03.540007967Z`. Local tag: `eta-api:local`.
- Closeout regression check: all 70 tests pass with `-W error` in the project
  environment (4.175 seconds) and the existing clean pinned macOS environment
  (4.159 seconds). This is not a full test-suite run inside Linux.
- Dockerfile/ignore behavior is unchanged from the user's final files; only
  missing final newlines were added. No application/dependency/model changes.
  `git diff --check` passes; model/metadata SHA-256 checks confirm both are unchanged.

## Remaining limits

- Only local Linux ARM64 serving is smoke-tested. No AMD64, full Linux-suite,
  load/concurrency, vulnerability-scan, or comprehensive cross-platform checks.
- Shared requirements still contain test-client packages. Dependency separation
  is deferred; keep exact model-runtime compatibility checks intact.
- The Python/base-image pin is a local compatibility baseline. Revisit a tested
  runtime upgrade and security scan before any public deployment.
- No image-registry upload, cloud service, TLS/authentication, durable prediction
  logging, database, live replay, or monitoring. No new model-quality claim.

## Previously verified API/model state

- 70 tests pass with `-W error` in both the project and fresh pinned environment
  (12 new API tests). API tests cover startup/shutdown, one-time loading, parity
  for 120 generated requests, request failures, missing/corrupt artifacts, runtime
  compatibility, server errors, and localhost CLI settings.
- `uv pip check` passes in both environments. Fresh verification environment:
  `/private/tmp/eta-api-check.4Y7ZC9/venv` (temporary/local, not a runtime requirement).
- Real HTTP smoke test of the full model: health 200, prediction 200 at 43.63
  minutes, incomplete and malformed JSON 422. Foreground server stopped cleanly;
  no listener remains on port 8000. The fresh environment also loads the full
  saved artifact and matches the Python/CLI response exactly.
- The example JSON produces 43.63 minutes in both environments, tagged with the
  saved model checksum. This is an example prediction, not a model-quality metric.
- The previous interface audit reproduced all 13 original model features exactly for
  all 116,667 source rows, including cancellations with outcomes stripped away.
- Artifact: `artifacts/eta_2026_jan_aug/model.joblib` (478,252 bytes), with
  `metadata.json` beside it. Both are intentionally local/Git-ignored.
- Saved model SHA-256:
  `29447c8ee3ac6ac62d0f72b61d43f24668d01ed62b7974266b9f7991d3ca5dcd`.
- 160 trees trained on 113,079 delivered rows. Reloaded predictions equal the
  original predictions exactly on every row. Separate-process prediction checks
  match between the existing and fresh pinned environments, without outcome inputs.
- Source/model/code checksums checked. The raw dataset and simulator are unchanged.
- The previous model's August MAE was 3.196 minutes; this is NOT a performance
  measurement of the full-data artifact. January–August is now training data.
- Unrelated `AGENTS.html` and `AGENTS_files/` remain untouched and untracked.

## Open follow-ups

- [x] Daman reviewed the host option and implemented the Docker startup command.
- [x] Local serving smoke checks and container removal completed with the evidence above.
- [x] Define a reviewable local logging contract and record the $0 budget.
- [x] Daman reviewed and accepted the retry/durability semantics.
- [x] Start/authenticate local PostgreSQL and test named-volume persistence.
- [x] Remove the temporary persistence probe and independently verify zero user tables.
- [x] Rename the bootstrap superuser to `eta_admin`, reset the verified-empty named
  volume, recreate/verify it, and review the publishable Compose files.
- [ ] In a separate step, create a distinct least-privileged `eta_app` login and
  the first migration; never give the API the administrator credential.
- [ ] Decide non-feature run context and operational attempt logging before API
  integration; agree cancellation availability before terminal-outcome replay.
- [ ] Agree on a later untouched evaluation window for the refitted model.
- Cloud deployment is deferred, not a prerequisite for these local steps.

## Context for the next session

This is a learning-first project with explicit approval per step. Passing unit
tests is not permission to take over later Docker work or another release, or
proof of model quality. Preserve the recorded August results; do not call post-refit
in-sample scores held-out performance. Use `models.refit_eta.load_artifact` to load
the trusted model; Joblib can execute code, and checksums are not signatures.
See the README's local HTTP API section for commands and the response contract,
and [session-log.md](session-log.md) for implementation details and checks.
Verify publication with `git log -1`, `git status`, and
`git ls-remote origin refs/heads/main`. The commit containing this logging design
must match remote main; old session entries preserve their pre-approval state.
The corrected PostgreSQL service is intentionally local and running. Follow the
AGENTS least-privilege design step next. Do not expose either service publicly,
give the API administrator credentials, or infer permission to implement the role,
migration, persistence, or replay from the accepted logging design.
