# Handoff — September 3, 2026

## Session topic

Closed out Daman's user-built local Docker API packaging after its serving smoke
checks passed. Daman explicitly approved documenting and committing/pushing the
reviewed Dockerfile and ignore file. The API host option is published in `c7c880b`,
the original API in `8f1159f`, and Python/CLI interface in `6e8c0aa`.
Codex reviewed files, reran Python tests, inspected image metadata/container
removal read-only, and prepared the closeout; it did not rebuild or start a service.
The saved full-data model is unchanged. Cloud deployment is a separate next
planning decision, not authorized implementation.
The earlier [evaluation record](evaluation-2026-09-03.md) remains unchanged and
describes the pre-refit model, not a held-out score for the new artifact.

## Key decisions

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
- No full-data retraining, database, replay, cloud deployment, or infrastructure.

## Docker smoke evidence and current closeout checks

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
- [ ] Propose a bounded cloud deployment plan covering host/architecture, access
  control, artifact delivery, and costs; wait for approval before provisioning.
- [ ] Agree on a later untouched evaluation window for the refitted model.

## Context for the next session

This is a learning-first project with explicit approval per step. Passing unit
tests is not permission to take over later Docker work or another release, or
proof of model quality. Preserve the recorded August results; do not call post-refit
in-sample scores held-out performance. Use `models.refit_eta.load_artifact` to load
the trusted model; Joblib can execute code, and checksums are not signatures.
See the README's local HTTP API section for commands and the response contract,
and [session-log.md](session-log.md) for implementation details and checks.
Verify publication with `git log -1`, `git status`, and
`git ls-remote origin refs/heads/main`. The commit containing this Docker closeout
must match remote main; old session entries preserve their pre-approval state.
The README now includes Docker build/run/check/stop commands. No `eta-api`
container remains. Do not expose this unauthenticated service publicly or infer
permission to deploy from the completed local checks.
