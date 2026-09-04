# Handoff — September 3, 2026

## Session topic

Implemented the approved optional API `--host` argument and its test, preserving
the localhost default and one worker. The original API is published in `8f1159f`;
the earlier Python/CLI interface is in `6e8c0aa`. This change follows the standing
commit/push workflow, excluding Daman's uncommitted Docker packaging files.
Docker learning is user-led; only this host-option change was delegated to Codex.
The saved full-data model is unchanged. Container HTTP serving and cloud
deployment remain future steps.
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

## Current host-option check

- The expanded CLI test first failed against the old implementation because
  `--host` was unrecognized, then all 12 API tests passed with `-W error`.
- Full suite: 70 tests pass with `-W error` in both the project environment and
  the existing clean pinned environment listed below. CLI `--help` exposes the new
  optional argument. Server startup is mocked in the CLI test; no network
  listener was started and no Docker rebuild was run by Codex in this step.
- `git diff --check` passes. Before/after SHA-256 checks confirm the model binary,
  metadata, Dockerfile, and ignore file are unchanged by this step.
- Daman pasted successful Linux ARM64/non-root image configuration, dependency
  consistency, and API/model-package import checks. The initial file listing
  revealed simulator/cache files: Codex's `!code/` exception mistakenly allowed
  descendants. The corrected allowlist omits directory-only exceptions.
- Daman reported the corrected image checks succeeded. Mac/Linux single-request
  prediction commands were supplied and accepted, but their output was not
  pasted or independently verified. Recheck parity after rebuilding the API.
- `Dockerfile` and `.dockerignore` remain user-owned, untouched and uncommitted.
  The Dockerfile still prints Python's version by default; it does not serve HTTP.
  The built image predates this host-option change. Requirements still include
  test-client dependencies; splitting runtime/test requirements is deferred.

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

- [ ] Daman reviews the host-option change and its verification.
- [ ] Next user-led step: change the Docker startup command, rebuild, run with a
  read-only model mount and localhost-published port, then verify health,
  prediction parity, invalid requests, and shutdown. Do not start it automatically.
- [ ] Review/publish Docker packaging separately once its agreed checks pass.
- [ ] Agree on a later untouched evaluation window for the refitted model.

## Context for the next session

This is a learning-first project with explicit approval per step. Passing unit
tests is not permission to take over later Docker work or another release, or
proof of model quality. Preserve the recorded August results; do not call post-refit
in-sample scores held-out performance. Use `models.refit_eta.load_artifact` to load
the trusted model; Joblib can execute code, and checksums are not signatures.
See the README's local HTTP API section for commands and the response contract,
and [session-log.md](session-log.md) for implementation details and checks.
Verify publication with `git log -1`, `git status`, and the remote main commit;
the old session-log entry marked uncommitted describes the pre-approval snapshot.
Do not expose this unauthenticated service publicly. No server is left running.
