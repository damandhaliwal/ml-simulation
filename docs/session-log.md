# Session Log

Newest entries first. Repository-local session capture and handoff, so the notes
travel with the code; no global session log or unrelated history was modified.

## 2026-09-04 12:22 EDT — Local logging contract and zero-dollar scope

### Decisions and scope

- Daman clarified a hard $0 budget and the goal of demonstrating production-grade
  ML skills. Cloud Run was discussed but no cloud resources were created; cloud
  deployment is deferred, not a prerequisite for operational ML testing locally.
- Daman approved continuing with the proposed prediction/outcome logging contract
  before implementing persistence. This is a documentation-only design draft for
  review, not approval to install PostgreSQL, change the API, or start replay.
- Added [prediction-logging.md](prediction-logging.md), linked it from README,
  and updated AGENTS/handoff to keep the current goal, budget, and next step clear.

### Proposed behavior and open decisions

- Retain run provenance, immutable confirmation-time predictions, and separately
  observed outcomes. Join on `(run_id, order_id)`; retries must not multiply orders.
- Proposed first-write-wins idempotency and commit-before-success mean a storage
  failure would produce an explicit API error. Review this availability/auditability
  tradeoff before implementation; today's API has no persistence.
- Separate intended confirmation time, actual replay prediction time, simulated
  outcome availability/observation, wall-clock recording, and model-call timing.
  Failed attempts and HTTP timing need operational logs, not fake prediction rows.
- The generator has no cancellation timestamp. A cancellation observation policy
  must be agreed before terminal-outcome replay; none was invented in the draft.
- Local storage setup, non-feature run/time context, attempt telemetry, a later
  evaluation window, and cancellation timing remain separate approval decisions.

### Verification and boundaries

- `PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -q`:
  70 existing tests pass in 4.157 seconds. This is a current macOS regression run,
  not evidence that the proposed logging/replay behavior is implemented.
- Focused in-memory source checks: 13 request fields, 13 model features, correct
  Toronto-local derivation, no labels in either input snapshot, null cancellation
  labels/no `cancelled_at`, and the illustrative 43.63 - 50 = -6.37 error.
- `shasum -a 256` confirms the model and metadata retain the prior recorded hashes;
  `git diff --check` passes. No new evaluation dataset or full-data training run.
- Only documentation is published under the standing commit/push agreement.
  No runtime, dependency, Docker, database, or service changes; unrelated untracked
  `AGENTS.html` / `AGENTS_files/` remain untouched. Old log entries are historical.
- Next: Daman reviews the draft and approves one local persistence step. Do not
  treat publishing this design as acceptance of every proposed behavior.

## 2026-09-03 23:04 EDT — Local Docker API closeout

Project: ml-simulation. Duration: extended user-led Docker learning session.

### Decisions

- Daman created, built, and ran the Docker packaging with instructions; Codex
  implemented only the separately approved API host option in `c7c880b`.
- After the serving checks and container removal, Daman explicitly approved
  Codex documenting results and committing/pushing the reviewed packaging files.
  This is source publication, not an image upload or cloud deployment.
- Keep the existing trusted model outside the image, mounted read-only at
  `/model`. Run one non-root API worker on container `0.0.0.0:8000`, publishing
  only host `127.0.0.1:8000`. No training occurs during build/startup/prediction.
- Preserve the corrected default-deny source allowlist. No agent instructions,
  docs, simulator, tests, caches, data, or model binaries belong in the app copy.
- Documentation distinguishes pasted HTTP evidence, user-reported file checks,
  read-only Docker metadata checks, and automated macOS tests. The session-capture
  skill uses the existing repo-local log/handoff; no global records or old entries
  were changed or pruned.

### User-run Docker evidence

The HTTP timestamps below are September 4 UTC / September 3 EDT.

| Check | Result | Evidence |
| --- | --- | --- |
| Build/runtime | Linux ARM64, UID/GID 10001, Python 3.13.7; imports and dependency check pass | User-pasted output |
| Corrected application files | Requirements plus nine Python files; no simulator/caches | User reported success after correction; listing not repasted |
| Health, 01:25:34 UTC | 200, ready, expected model checksum, simulated | User-pasted HTTP response |
| Prediction, 01:29:47 UTC | 200, EXAMPLE-001, 43.63 minutes, same checksum, simulated | User-pasted HTTP response |
| Empty object, 01:29:57 UTC | 422, missing-fields detail, no prediction | User-pasted HTTP response |
| Malformed JSON, 01:30:11 UTC | 422, Body must be a valid JSON object | User-pasted HTTP response |
| Stop/removal | No matching eta-api container after stopping the API terminal process | User report plus Codex read-only confirmation |

- The expected model checksum is
  `29447c8ee3ac6ac62d0f72b61d43f24668d01ed62b7974266b9f7991d3ca5dcd`.
- The initial filter incorrectly used `!code/`, re-including descendants. Codex
  acknowledged the error; Daman removed directory-only exceptions and rebuilt.
  Final patterns allow only requirements/packaging and Python files directly in
  models/prep/serving. No broad source-directory exception remains.
- The first shutdown check listed the still-existing container. Daman then
  stopped the foreground process and repeated the check successfully. Do not
  infer an observed graceful-shutdown log or exit code; neither was captured.
- The example matches the earlier Mac prediction. It is one portability smoke
  check, not comprehensive Linux/Mac parity or a model accuracy evaluation.

### Closeout verification by Codex

```sh
PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -q
PYTHONPATH=code /private/tmp/eta-api-check.4Y7ZC9/venv/bin/python -W error -m unittest discover -s tests -q
docker image inspect eta-api:local
docker ps -a --filter 'name=^/eta-api$' --format '{{.Names}} {{.Status}}'
git diff --check
```

- All 70 tests pass in the project environment (4.175 seconds) and the existing
  clean pinned macOS environment (4.159 seconds), with warnings treated as errors.
- Read-only image inspection confirms the final API CMD, Linux ARM64, numeric
  non-root user, `/app` working directory, and exposed `8000/tcp` metadata.
  Final local image ID:
  `sha256:a47342eac379289f72ac62be109f7337828a740df3d8b4d8cb2dea3f6da48443`.
  This is the rebuilt serving image, not the earlier Python-version diagnostic image.
- Read-only filtered container listing returned no output. No service was started,
  rebuilt, stopped, or deployed by Codex during closeout. Existing model/metadata
  checksums and Git-ignore status were checked; no artifact enters the commit.

### Artifacts, limits, and follow-ups

- Add the reviewed Dockerfile and `.dockerignore`; normalize only their missing
  final newlines. Update README run/check/stop instructions, AGENTS current state,
  this log, and handoff. No application code or dependency changes.
- Exclude unrelated `AGENTS.html` / `AGENTS_files/`, ignored data, models, and
  environments from publication. Preserve old log entries as historical records.
- Publication is the commit containing this entry; verify remote main using
  the Git commands in the handoff. No image-registry upload is part of this step.
- [ ] Review a small cloud plan with explicit cost/access/artifact decisions before
  any provisioning. Future implementation remains learning-first and approval-gated.
- Deferred: runtime/test dependency split, full Linux tests, AMD64, vulnerability
  scan/runtime upgrade, load tests, authentication/TLS, image registry, database,
  replay, monitoring, and a later untouched model-evaluation window.

## 2026-09-03 21:16 EDT — Optional API host for user-led Docker packaging

Project: ml-simulation. Duration: extended learning conversation, bounded code change.

### Decisions

- Daman is writing/running the Docker packaging himself to learn it. He delegated
  only the proposed API host-option change and test to Codex. Later Docker
  startup, rebuild, HTTP checks, and cloud work must not run ahead of his review.
- `main()` accepts optional `--host`, defaulting to `127.0.0.1`, and forwards it
  to Uvicorn with the existing port validation and one worker. Model loading,
  request validation, and prediction code are unchanged. No new dependency.
- `0.0.0.0` is intended for the container listener; later Docker port publication
  must bind the Mac side to `127.0.0.1`. Do not expose this unauthenticated API.
- Follow the standing commit/push workflow for this change and its documentation
  only. Preserve the untracked Dockerfile, ignore file, and unrelated HTML exports.
- Use the session-capture skill with existing repository-local records; no
  global memories/logs or historical entries are edited or pruned.

### Checks and results

```sh
PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -p test_api.py -v
PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -q
PYTHONPATH=code /private/tmp/eta-api-check.4Y7ZC9/venv/bin/python -W error -m unittest discover -s tests -q
PYTHONPATH=code .venv/bin/python -W error -m serving.api --help
git diff --check
```

- Red/green: the new explicit-host case failed on the old code with unrecognized
  arguments; after implementation all 12 API tests passed (0.274 seconds).
  Both default/explicit addresses, port 8123, one worker, and deferred model
  loading are asserted. Mocked Uvicorn means the CLI test opens no network port.
- Full suite: 70 tests pass with warnings treated as errors in the project
  environment (4.191 seconds) and the previously created clean pinned environment
  (4.176 seconds). Tests fit temporary fixtures, not the saved full-data model.
- Diff whitespace check passes. Before/after SHA-256 checks confirm the saved
  model binary, metadata, Dockerfile, and ignore file are unchanged.
- Help text shows `--host` and the unchanged localhost default. No real server,
  Docker build, dependency installation, or full-data training ran in this step.

### Docker evidence and correction

- Daman built the image locally and pasted successful Linux ARM64 / UID 10001
  configuration, dependency consistency, and model/API import results. pip's
  unwritable-cache warning is avoided with `python -m pip --no-cache-dir check`.
- The initial file listing exposed a mistake in Codex's ignore instructions:
  `!code/` re-included descendants. The corrected file starts with `**` and allows
  only packaging files, requirements, and Python files in models/prep/serving,
  without directory-only exceptions. Daman reported the corrected checks passed.
- Mac/Linux example prediction commands were supplied and accepted; their
  outputs were not captured. Do not describe them as independently verified.
- Docker packaging remains uncommitted and was not edited by Codex. Its current
  CMD prints Python's version; the existing image does not contain this API edit.

### Artifacts and follow-ups

- Modified API/test plus README, AGENTS, this session log, and handoff. No model,
  dataset, dependency, Dockerfile, or ignore-file change in this implementation.
- [ ] Daman reviews this step before the container startup command is changed.
- [ ] Rebuild later and verify container health, prediction parity, invalid
  requests, and shutdown with a read-only artifact mount and local host port.
- [ ] Runtime/test dependency separation and Docker publication remain separate.
- [ ] Cloud deployment and an untouched future model-evaluation window remain open.

## 2026-09-03 18:12 EDT — Local prediction API

Project: ml-simulation. Duration: brief conversation, bounded implementation.

### Decisions

- After reviewing the remaining roadmap, Daman approved the next local API step
  only. Docker, cloud resources, database logging, live replay, and retraining
  remain separate steps. The introductory update confirmed the normal Git
  commit/push workflow for this step.
- The prior interface's no-commit pause was resolved by explicit publication
  approval; that interface is published in `6e8c0aa`. Old log entries preserve
  their historical pre-approval state rather than being rewritten.
- Reuse `validate_request` and `load_artifact` unchanged. Add a small FastAPI app
  factory and foreground Uvicorn command, bound to `127.0.0.1` with one worker.
- Load the explicitly selected trusted artifact once at startup. Do not train,
  choose a default model silently, or reload the artifact per request. Restart
  the service to load a changed artifact. Missing/corrupt/incompatible models
  abort startup; health means a model is loaded, not that its accuracy is good.
- Keep request/domain failures separate from inference failures: HTTP 422 for
  invalid requests, 503 outside the loaded lifecycle, unexpected server errors
  remain 500. Invalid request bodies are not echoed in error responses.

### Implementation walkthrough

- `code/serving/api.py`:
  - `create_app(artifact_dir)` returns a FastAPI app without file loading at
    import/construction time. Its `lifespan` loads `(model, metadata)` at startup
    and clears the retained reference on shutdown.
  - `loaded_artifact()` checks readiness and returns the in-memory pair.
  - `invalid_body(...)` turns FastAPI body-validation failures into a simple
    JSON error, avoiding raw/nonfinite input values in error serialization.
  - `health()` returns readiness, the exact model SHA-256, and the synthetic flag.
  - `predict(payload)` applies the shared validator and invokes the saved model;
    the four response fields match the Python/CLI interface. It is synchronous
    so FastAPI runs prediction off the async event loop.
  - `main()` requires `--model-dir`, accepts a validated port, and starts one
    localhost worker. Ctrl+C shuts it down. No daemon/background setup added.
- Added `code/serving/__init__.py` and 12 API tests in `tests/test_api.py`.
- Extended pinned requirements with the API/server/test client and dependencies.
  Original model-package versions and the user's pre-existing XGBoost install
  were preserved. No new modeling package or system dependency was installed.
- Updated README, AGENTS, and handoff to reflect the actual API state and the
  resolved interface publication pause. The session-capture skill keeps this
  log and handoff repo-local; no global memories or old log entries were changed.

### Dependency findings

- The first install selected HTTPX; Starlette 1.6.0 warns that its test client now
  expects HTTPX2. Replaced the newly installed HTTPX/HTTPCore with HTTPX2 2.12.0
  and HTTPCore2 2.12.0, as documented by Starlette. The removed packages were
  introduced only during this step and can be reinstalled if ever needed.
- AnyIO 4.15.0 deprecates aliases still imported by Starlette 1.6.0. Pinned AnyIO
  4.14.2 after inspecting the import failure; no warnings were suppressed and no
  installed third-party source was patched. Both environment checks pass.
- Direct entry points: FastAPI 0.141.1, Uvicorn 0.52.4, HTTPX2 2.12.0. All 25
  required packages, including transitive dependencies, are pinned in requirements.

### Checks and results

```sh
PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -q
PYTHONPATH=code /private/tmp/eta-api-check.4Y7ZC9/venv/bin/python -W error -m unittest discover -s tests -q
uv pip check --python .venv/bin/python
uv pip check --python /private/tmp/eta-api-check.4Y7ZC9/venv/bin/python
PYTHONPATH=code .venv/bin/python -W error -m serving.api \
  --model-dir artifacts/eta_2026_jan_aug --port 8000
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/predict \
  -H 'Content-Type: application/json' \
  --data-binary @docs/prediction-request.example.json
git diff --check
```

- 70 tests pass with warnings as errors in both environments. The fresh CPython
  3.13.7 environment was created from `requirements.txt` only. Both dependency
  consistency checks pass (project environment also retains existing XGBoost).
- Tests verify one-time loading, startup/shutdown readiness, no fitting during
  prediction, exact parity for 120 generated requests, invalid/malformed/nonfinite
  input rejection, artifact failures, server-vs-client errors, and CLI binding.
- A real Uvicorn listener served the full-data artifact: health 200, example
  prediction 200 at 43.63 minutes, incomplete request 422, malformed JSON 422.
  Local socket binding/calls required sandbox approval. The foreground process
  was stopped cleanly with Ctrl+C; port 8000 has no remaining listener.
- The clean environment also loads the actual full-data artifact and returns
  the identical Python/CLI/API response. Dataset and original LightGBM/refit
  source hashes match artifact metadata. Saved model SHA-256 remains
  `29447c8ee3ac6ac62d0f72b61d43f24668d01ed62b7974266b9f7991d3ca5dcd`.
- These checks verify serving behavior, not new model accuracy. No held-out
  evaluation or full-data retraining was performed.

### Sources used for implementation

- [FastAPI lifespan and model loading](https://fastapi.tiangolo.com/advanced/events/)
- [FastAPI lifecycle tests](https://fastapi.tiangolo.com/advanced/testing-events/)
- [Starlette TestClient / HTTPX2](https://www.starlette.io/testclient/)

### Open questions and follow-ups

- [ ] Daman reviews the API behavior and local run commands.
- [ ] Agree on Docker packaging and Linux artifact compatibility checks next.
- [ ] Keep cloud deployment, authentication, costs, database/replay, and a later
  untouched model-evaluation window as explicitly approved future steps.

### Context

This is a small unauthenticated localhost API, not a public production service.
There is no traffic/load benchmark, TLS, rate limit, or durable prediction log.
No server remains running. Use `git log -1`, `git status`, and the remote main
commit to verify publication; do not infer readiness for Docker from passing tests.

## 2026-09-03 17:18 EDT — Validated local prediction interface (uncommitted)

Project: ml-simulation. Duration: brief conversation, bounded implementation.

### Decisions

- Daman approved moving on to the local prediction interface and explicitly
  requested **no commit after implementation** so he can review first. Leave
  changes unstaged/uncommitted; do not push. This overrides automatic publishing
  for this step and is recorded in AGENTS/handoff for the next session.
- Accept one strict JSON object, not a batch or full simulator row. Return order
  ID, predicted duration in minutes, exact model-file SHA-256, and `simulated`.
- Derive Toronto-local hour/day from a timezone-aware confirmation timestamp.
  Keep the remaining feature definitions, trained model, and preprocessing intact.
- Reject missing/extra fields (including outcomes), unknown categories, boolean
  or string numerics, nonfinite values, invalid counts/ranges, and contradictory
  weather under the existing simulator rules. Do not invent empirical upper
  limits or claim a valid request is in the training distribution.
- Reuse the trusted artifact loader and existing dependencies. Each prediction
  call reloads the artifact; no caching, endpoint, UI, logging store, replay,
  deployment, full-data retraining, or new accuracy evaluation in this step.

### Implementation walkthrough

- `code/models/predict_eta.py`:
  - `REQUEST_FIELDS` names the 13 request fields: ID, timestamp, and the 11
    non-derived model inputs. `COUNT_MINIMUMS` records the four count constraints.
  - `validate_request(request)` returns a fresh dictionary with all 13 model
    features, deriving hour/day and excluding identifiers/timestamps from the
    feature row. It leaves the input unchanged and raises field-specific errors.
  - `predict_eta(request, artifact_dir)` validates before loading the model,
    calls the existing predictor, and returns a response dictionary. No fit call.
  - `main()` reads `--input` JSON and an explicit trusted `--model-dir`; stdout
    contains only the JSON response on success. Ordinary input/file/compatibility
    errors produce stderr and exit code 2, not a replacement prediction.
- `docs/prediction-request.example.json` is a hand-authored synthetic example,
  deliberately without outcomes. It is not a generated dataset or a real order.
- `tests/test_predict_eta.py` adds 15 tests for validation, date/year/DST edges,
  input immutability, loader invocation, response identity, no fit on prediction,
  feature parity, and CLI success/failure. CLI integration tests fit only tiny
  temporary fixtures; the saved full-data artifact is never overwritten.
- README documents the exact contract, Python/CLI usage, and limitations.
  The session-capture skill keeps this log/handoff repository-local; no global
  memory, session archives, or unrelated HTML exports were changed.

### Checks and results

```sh
PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -v
PYTHONPATH=code /private/tmp/eta-review-env.xmKqa0/venv/bin/python -W error -m unittest discover -s tests -q
PYTHONPATH=code .venv/bin/python -W error -m models.predict_eta \
  --model-dir artifacts/eta_2026_jan_aug \
  --input docs/prediction-request.example.json
git diff --check
```

- 58 tests pass in each environment, with warnings treated as errors.
- The CLI also runs in the fresh pinned environment; both return 43.63 minutes
  for `EXAMPLE-001`, with the unchanged model SHA-256
  `29447c8ee3ac6ac62d0f72b61d43f24668d01ed62b7974266b9f7991d3ca5dcd`.
- Full-corpus compatibility audit: select `REQUEST_FIELDS` from every one of
  the 116,667 raw orders, run `validate_request`, and compare the resulting
  dictionary to the original `ALL_FEATURES`. Every value matches, including
  calendar derivations. Cancellation outcomes are not sent to the interface.
- Source dataset, model, and existing LightGBM/refit implementation checksums
  match the saved metadata. No full model refit or saved artifact changes.
- These are software/feature-parity checks, not evidence of predictive accuracy.
  The old August MAE still belongs only to the pre-refit model.

### Artifacts and publication state

- New: `code/models/predict_eta.py`, `tests/test_predict_eta.py`, and the JSON example.
- Updated: README, AGENTS, handoff, and this log. No dependencies changed.
- All interface work remains unstaged/uncommitted. Latest published commit is
  still `a6bb835`; no commit or push was attempted during this step.

### Open questions and follow-ups

- [ ] Daman reviews the implementation and request/response contract.
- [ ] Publish only after his explicit approval.
- [ ] Agree on any subsequent API/serving step and a later untouched evaluation
  window; neither is implemented or authorized by passing these tests.

## 2026-09-03 17:01 EDT — Full-data refit and local model artifact

Project: ml-simulation. Duration: brief conversation, bounded implementation.

### Decisions

- Daman approved refitting all 113,079 delivered orders at the previously selected
  160 trees, saving the model/metadata, checking reload predictions, and publishing
  code/documentation only. No further acceptance question or model tuning was needed.
- Kept the original simulator, dataset, feature order, categorical mappings, and
  other LightGBM settings unchanged. The 28 previously excluded boundary labels
  are eligible now; the 3,588 cancelled rows still cannot train the duration target.
- Observation time is explicitly `2026-09-03T00:00:00Z`, after the last delivery
  at `2026-09-01T04:42:27.651978+00:00`. All confirmations/outcomes must precede it.
- Persist the fitted wrapper with Joblib (an existing dependency), plus readable
  JSON metadata. Require a new output directory; do not overwrite prior artifacts.
- Keep `artifacts/` Git-ignored. The session-capture skill updates this repository's
  log and handoff; no global memory or unrelated files are modified.

### Implementation walkthrough

- `code/models/refit_eta.py` contains the new workflow:
  - `refit_eta(data, output_dir, observed_at=...)` validates raw orders and label
    availability, fits exactly 160 trees with no validation set, writes the model,
    checks every delivered-row prediction after reload, writes metadata, then
    verifies the public loader. It returns the metadata dictionary.
  - `load_artifact(directory)` returns `(model, metadata)` after checking the
    format, feature contract, exact runtime versions, checksum, fitted state,
    and tree count. Only load our own trusted artifacts: Joblib can execute code.
  - `feature_contract()` records target, prediction moment, feature order, category
    maps, unknown-category code, matrix dtype, floor, and rounding.
  - `file_sha256(path)` records/verifies file content; `runtime_versions()` records
    Python and the eight pinned package versions. `main()` exposes the CLI.
- The refit calls `load_orders` and `separate_cancellations`, not `prepare_dataset`:
  using the latter would incorrectly discard the 28 now-observed boundary labels.
- Existing -1 unknown-category, 5-minute floor, and 2-decimal rounding values are
  named constants in `lightgbm_eta.py` so inference and metadata share them.
  Values and prediction behavior did not change.
- Metadata includes source/model/implementation hashes, training/source windows,
  latest delivery, observation/training timestamps, row counts, actual tree count,
  parameters, feature contract, runtime/platform, and full-row roundtrip count.
- Tests cover full-row inclusion/no validation set, unavailable labels, naive
  observation times, no delivered labels, no overwrite, incompatible metadata,
  corruption before deserialization, and failed roundtrip before metadata publication.

### Checks and results

```sh
PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -v
PYTHONPATH=code /private/tmp/eta-review-env.xmKqa0/venv/bin/python -W error -m unittest discover -s tests -q
PYTHONPATH=code .venv/bin/python -W error -m models.refit_eta \
  --data data/orders_2026_jan_aug.json \
  --output-dir artifacts/eta_2026_jan_aug \
  --observed-at 2026-09-03T00:00:00Z
git diff --check
git check-ignore -v artifacts/eta_2026_jan_aug/model.joblib artifacts/eta_2026_jan_aug/metadata.json
```

- 43 tests pass with warnings treated as errors in both environments (seven new tests).
- Actual refit: 113,079 delivered rows, exactly 160 trees, no early stopping.
- Exact public predictions match before/after reload on all 113,079 delivered
  orders. Separate Python processes in both pinned environments also agree on
  all predictions after removing outcome fields from input rows.
- Cross-process prediction digest (ordered little-endian float64 outputs):
  `2d4ac689f8317720ab6f58fabb170201f2e11148e608983136721999339f809f`.
- Model file: 478,252 bytes; SHA-256
  `29447c8ee3ac6ac62d0f72b61d43f24668d01ed62b7974266b9f7991d3ca5dcd`.
- Source dataset and implementation hashes match the metadata. The source hash
  remains `be32c722f37cbe6a80ce25a45de70765765b512fd8309e57ace7b2c28d9d5666`.
- Both artifact files are ignored by Git. No environment/dependency changes.

### Artifacts

- Added code/tests: `code/models/refit_eta.py`, `tests/test_refit_eta.py`.
- Updated: `.gitignore`, `code/models/lightgbm_eta.py`, README, AGENTS, and handoff/log.
- Local only: `artifacts/eta_2026_jan_aug/model.joblib` and `metadata.json`.
- Preserved unchanged: `docs/evaluation-2026-09-03.md`, simulator, raw dataset,
  and unrelated HTML exports.

### Open questions and follow-ups

- [ ] Agree on the next bounded prediction interface/input-validation step.
- [ ] Agree on a later untouched window to evaluate the full-data model.

### Context

The refit is complete, not merely proposed. It now uses August for training, so
the earlier 3.196-minute August MAE does not evaluate this artifact. No in-sample
metric has been presented as generalization evidence. There is no API, deployment,
automatic promotion, new dependency, or permission to begin the next step.

## 2026-09-03 16:42 EDT — Review corrections and frozen ETA evaluation

Project: ml-simulation. Duration: brief conversation, substantive implementation.

### Decisions

- Daman approved fixing both review issues (label timing, environment) and all
  flags (default test scoring, segment evaluation, warnings/whitespace), with
  documentation and confirmation of results before a conditional full-data refit.
- Kept the simulator and original dataset unchanged. Did not tune model settings
  or features; corrected eligibility and evaluation behavior only.
- Used exclusive label cutoffs at the following split's start, not at the first
  sampled order. Otherwise a quiet gap could incorrectly admit unavailable labels.
  Equality is excluded for a conservative, explicit snapshot rule.
- Test includes every delivered August confirmation, even when delivery is on
  September 1. No artificial end-of-August truncation of slow deliveries.
- Added an explicit test-scoring opt-in to both evaluation functions and CLIs;
  kept train/validation as defaults. Added the existing heuristic to the combined
  model comparison. No automatic promotion or full-data refit was added.
- Used repo-local locations supported by the session-capture skill for logs and
  handoff. The validation skill guided boundary, denominator, and metric checks.

### Implementation walkthrough

- `parse_timestamp(value)` in `code/prep/dataset_validation.py`: ISO timestamp
  string → timezone-aware UTC datetime. Rejects missing/naive timestamps instead
  of silently using the computer's local zone.
- `separate_cancellations(orders)` now checks IDs before any exclusion and verifies
  delivered timestamps against duration. This stops duplicates or contradictory
  labels from disappearing behind a filter.
- `filter_available_labels(splits, label_cutoffs)` → `(eligible, unavailable)`
  dictionaries of original rows. For example, a June 30 23:59 confirmation
  delivered July 1 00:29 is counted as unavailable for training, not moved to July.
- `prepare_dataset(...)` constructs midnight cutoffs from the supplied ranges,
  filters labels, validates remaining splits, and returns excluded rows/counts.
  `validate_splits(...)` checks delivery timing as well as confirmation ordering.
  Without explicit cutoffs, direct validation at least rejects labels at/after
  the first confirmation in the next split.
- `compute_segment_metrics(orders, predictions)` in `code/models/baselines.py`
  returns per-group counts and errors for weather, pickup zone, hour, distance,
  and idle couriers. Each dimension partitions the same scored population.
  `print_segment_metrics(results)` displays these diagnostics when `--segments`
  is requested. Absent groups are not silently interpreted as zero-error groups.
- Both `evaluate_*` functions preserve overall metric keys, add `count` and
  validation/test `segments`, and take keyword-only `include_test=False`.
  No test inputs are read by those functions unless explicitly enabled.
- LightGBM's fit uses the installed 4.7 API's `eval_X`/`eval_y` arguments instead
  of deprecated `eval_set`. Removed global/test warning filters. The feature-gain
  display also handles an all-zero-gain tiny model without dividing by zero.
- `requirements.txt` pins NumPy, scikit-learn, LightGBM, and their runtime
  dependencies. README now describes the actual stage, setup, CLI flags, cutoffs,
  metrics, synthetic-only scope, and the limits of a later full-data refit.

### Checks and results

```sh
PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -v
PYTHONPATH=code .venv/bin/python -W error -m prep.dataset_validation
git diff --check a0b2601
shasum -a 256 data/orders_2026_jan_aug.json
```

- 36 tests pass, with no warnings; nine tests were added to the previous 27.
- Added coverage for strict label boundaries, default leakage detection,
  timezone/label consistency, duplicates before filtering, segment calculations,
  test opt-in, test-label independence, and prediction without outcomes.
- Dataset has 84,104 eligible training, 14,466 validation, and 14,481 test rows;
  16/12 delayed labels are retained separately. All 113,079 deliveries reconcile.
- Checksum matches the original dataset:
  `be32c722f37cbe6a80ce25a45de70765765b512fd8309e57ace7b2c28d9d5666`.
- Installed all eight pinned packages in `/private/tmp/eta-review-env.xmKqa0/venv`,
  using its own temporary uv cache. No existing project packages were changed:

```sh
uv venv --python .venv/bin/python /private/tmp/eta-review-env.xmKqa0/venv
uv pip install --cache-dir /private/tmp/eta-review-env.xmKqa0/cache --python /private/tmp/eta-review-env.xmKqa0/venv/bin/python -r requirements.txt
PYTHONPATH=code /private/tmp/eta-review-env.xmKqa0/venv/bin/python -W error -m unittest discover -s tests -v
uv pip check --cache-dir /private/tmp/eta-review-env.xmKqa0/cache --python /private/tmp/eta-review-env.xmKqa0/venv/bin/python
```

- Fresh environment: 36 tests pass; all eight packages are compatible. An initial
  `uv pip check` without the explicit temporary cache hit a sandbox permission
  error; the explicit-cache read-only retry succeeded.
- Full-data CLI smoke checks (using training/validation, not a full-data refit)
  confirm both model CLIs print segments, no test scores by default, and no warnings.
- Explicit August evaluation used frozen settings. A fresh-environment rerun
  reproduced 160 selected trees and LightGBM MAE 3.196 / bias -0.442 / P95 8.470 /
  RMSE 4.163 minutes. Independently recomputed all four from predictions, including
  linear interpolation for P95. Segment counts and weighted MAEs reconcile.
- Full overall and August segment numbers, interpretation, and reproduction
  commands are in [evaluation-2026-09-03.md](evaluation-2026-09-03.md).

### Open questions

- Is the roughly 6.5% MAE improvement versus linear regression sufficient for
  the prototype, given the greater underprediction and some worse segment tails?
- What later untouched window should evaluate the full-data model?

### Follow-ups

- [ ] Obtain acceptance of the reported model results, not merely the unit tests.
- [ ] If accepted, refit/save at 160 trees on all observed delivered labels and
  round-trip-check the artifact and metadata in a separate approved step.
- [ ] Preserve August's evaluation record and agree on the next held-out window.

### Artifacts and boundaries

- Modified: `AGENTS.md`, `README.md`, prep/model code, and focused tests.
- Added to version control as part of this step: previously untracked LightGBM
  code/tests, `requirements.txt`, this log, `handoff.md`, and the evaluation record.
- Unrelated browser exports `AGENTS.html` / `AGENTS_files/` are untouched.
- No model binary, raw data, secrets, environment, or generated cache is committed.
- No full-data refit, serving endpoint, deployment, or global memory update.
- Publication is the commit containing these notes; verify its remote hash using
  the Git commands in the handoff. Next work must not begin automatically.

### Context

The previous two commits implemented splits and baselines; the LightGBM source
was present but untracked. Earlier scripts exposed August by default, so we do
not claim it was never inspected. This session fixed the review issues and
validated frozen predictors without test-driven tuning. This is a synthetic
learning exercise, not evidence of real-world Toronto delivery accuracy.
