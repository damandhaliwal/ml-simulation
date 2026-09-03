# Handoff — September 3, 2026

## Session topic

Implemented the approved validated local prediction interface; awaiting Daman's
review. **Do not stage, commit, or push this step until he approves publication.**
The saved full-data model is unchanged; there is no API or deployment.
The earlier [evaluation record](evaluation-2026-09-03.md) remains unchanged and
describes the pre-refit model, not a held-out score for the new artifact.

## Key decisions

- One strict JSON request -> validated confirmation-time features -> existing
  artifact loader -> one duration prediction with order ID, model SHA-256, and
  synthetic-data label. No new dependency or model fitting during prediction.
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
- No new dependency, serving endpoint, replay, deployment, or infrastructure.

## Verified state

- 58 unit tests pass with `-W error` in both the project and fresh pinned environment
  (15 new tests). Tests include subprocess CLI success/failure, no retraining,
  feature parity, numeric validation, and Toronto date/year/DST boundaries.
- The example JSON produces 43.63 minutes in both environments, tagged with the
  saved model checksum. This is an example prediction, not a model-quality metric.
- The request validator reproduces all 13 original model features exactly for
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

- [ ] Daman reviews `code/models/predict_eta.py`, its tests, and the example request.
- [ ] Commit/push only after explicit publication approval for this step.
- [ ] Agree on any next serving/API step; no implementation or installation is
  authorized yet. The local function reloads the artifact on each call.
- [ ] Agree on a later untouched evaluation window for the refitted model.

## Context for the next session

This is a learning-first project with explicit approval per step. The usual
commit/push workflow is paused for this interface change by Daman's request.
Passing unit tests is not permission for another release or
proof of model quality. Preserve the recorded August results; do not call post-refit
in-sample scores held-out performance. Use `models.refit_eta.load_artifact` to load
the trusted model; Joblib can execute code, and checksums are not signatures.
See the README's local prediction section for the contract and runnable example,
and [session-log.md](session-log.md) for implementation details and checks.
The latest published commit is still `a6bb835`; the interface is a working-tree
change, not a published step. Check `git status` before any follow-up edits.
