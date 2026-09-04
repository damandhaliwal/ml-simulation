# Local prediction and outcome logging contract

Status: accepted by Daman on September 4, 2026. Nothing in this document is
implemented yet. The existing API still predicts without persistence.

## Objective and boundary

Demonstrate production-grade ML engineering locally within a hard **$0 budget**:
retain what the model knew, what it predicted, which model produced it, and what
became observable later. Cloud hosting is optional, not a prerequisite.

This step defines records and correctness rules, not a database implementation,
API change, replay engine, monitoring dashboard, or retraining job. The generator
remains stateless. Future replay must call the actual local HTTP API, not bypass
it with a direct model call. All records and reported results remain simulated.

## 1. Run context: make each experiment identifiable

One immutable manifest per replay run. A run means one experiment, not a courier
route or a batch of physically linked orders. A fresh experiment gets a new
`run_id`; resuming the same interrupted experiment retains it.

| Field | Meaning |
| --- | --- |
| `schema_version` | Start at `1`; describes this logging contract, not the model format. |
| `run_id` | Unique experiment identifier; paired with `order_id` because separate generator calls can reuse order IDs. |
| `simulated` | Always `true` for this project. |
| `source_sha256`, `source_order_count` | Identify the retained, immutable local source dataset and its number of unique orders. Reject duplicate IDs within it. |
| `scenario` | Generator seed, supplied controls, and confirmation-date window, including its Toronto timezone convention. No new evaluation window is selected here. |
| `code_commit`, `image_id` | Identify the clean source revision and actual local serving image used. Retain both; a mutable image tag is insufficient. |
| `model_sha256`, `model_metadata_sha256` | Identify the frozen model and retained metadata sidecar, including its feature contract and runtime versions. |
| `created_at_wall` | Actual UTC creation time of the experiment record. |

Keep source data, artifacts, and future logs local and Git-ignored; hashes alone
cannot reconstruct missing files. The source includes future outcomes and belongs
to the replay harness, not the prediction request. Freeze one model per run;
reject a response from another model. A challenger gets a separate run.

## 2. Prediction record: what we knew and returned

One successful logical prediction per `(run_id, order_id)`. The future API-side
logger captures this record. A request attempt is not necessarily a new prediction.

| Field | Meaning |
| --- | --- |
| `run_id`, `order_id` | Composite unique key and link to the source order. |
| `request_payload` | Exact accepted request object using `models.predict_eta.REQUEST_FIELDS`; preserve the original confirmation timestamp and its offset. |
| `features` | Exact dictionary returned by `validate_request`, including derived Toronto-local `local_hour` and `day_of_week`. This is the model input before its numeric/category encoding. |
| `predicted_delivery_duration_minutes` | Exact value returned by the existing model, including its existing floor and rounding; no second rounding or recomputation for logging. |
| `model_sha256` | Checksum from the actually loaded artifact, matching the run and HTTP response. Never infer it from a filename. |
| `predicted_at_simulated` | Actual replay time of the original prediction, supplied as non-feature context. Must equal the order's confirmation time for the initial confirmation-time replay. |
| `recorded_at_wall` | Actual UTC time when the prediction record is prepared for persistence; not a database commit timestamp. |
| `model_latency_ms` | Nonnegative elapsed time measured with a monotonic timer around `model.predict` only. Excludes validation, database writes, and HTTP round trip. |
| `simulated` | Must be `true` and agree with the loaded artifact. |

`confirmed_at` inside the request is the intended prediction moment. The harness
must pause simulated time during its request/retry sequence; it must not backdate
a prediction first made after advancing past confirmation. Keep actual replay
time separate from wall time. No outcome field, future batch/noise variable, or
promised deadline is added to the feature set.
Preserve the original promise separately with the outcome/source context below.

The current API rejects extra JSON fields. Run identity and replay time must
eventually reach the logger through separately agreed non-feature context; do not
insert them into today's request or assume headers/configuration are implemented.

### Retries, failures, and durability: proposed behavior

- Commit the prediction record before sending a successful response when logging
  is enabled. A failed database write must return an explicit service error, not
  an unlogged success or an in-memory fallback. This deliberately trades some
  availability for a complete audit trail in the learning system.
- An identical retry for the same key returns the original committed prediction
  without inserting another row or overwriting timestamps/timing. Compare accepted
  JSON objects structurally, ignoring key order; do not add timestamp-equivalence
  or other normalization rules silently. A different payload for that key is a
  conflict. A model mismatch is also a conflict, not a new prediction in that run.
- Enforce the unique key atomically, including concurrent duplicate requests.
  If the response is lost after commit, retrying must retrieve the committed row.
  This is idempotent persistence, not a claim of exactly-once network delivery.
- Invalid requests, prediction errors, client timeouts, and retries belong in
  operational attempt logs, not fake successful prediction rows. Those logs need
  distinct attempt IDs, available run/order correlation, status/error category,
  and a clearly named timing scope. Do not persist arbitrary invalid bodies or
  secrets. Their implementation is separate; the prediction table alone cannot
  measure all failures or end-to-end latency. A timeout does not prove no commit.

## 3. Outcome record: what became observable later

At most one terminal outcome per `(run_id, order_id)`, written by a separate future
outcome-ingestion path. Do not require a successful prediction to store an outcome:
an order whose API request failed still matters to coverage reporting.

| Field | Meaning |
| --- | --- |
| `run_id`, `order_id` | Same composite key as the source order/prediction. Reject orders absent from the run's source. |
| `confirmed_at`, `promised_delivery_at` | Original, immutable source timestamps; the promise never changes to match a prediction. |
| `status` | `delivered` or `cancelled`; absence of an outcome is not either terminal status. |
| `delivered_at`, `delivery_duration_minutes`, `late_delivery` | Original delivered labels; all three must be null for cancellations, never zero-filled. |
| `outcome_available_at_simulated` | Earliest simulated time at which this outcome is knowable. For deliveries, use `delivered_at`. Cancellation timing is unresolved below. |
| `observed_at_simulated` | Replay observation cutoff when the outcome was actually admitted, which can be later than its availability. |
| `recorded_at_wall` | Actual UTC time the outcome record is prepared for persistence. |
| `simulated` | Always `true`. |

Use timezone-aware timestamps and normalize stored outcome/observation times to
UTC. Check delivery duration against elapsed time with the existing validation
tolerance (`1e-6` minutes); require delivery after confirmation and a finite,
positive duration. Check `late_delivery` against delivery after the original
promise, not against the ETA. Re-ingesting the same source labels is a no-op that
preserves the first observation/recording times; conflicting labels are an explicit
error, never a silent overwrite.

### Availability and cancellation boundary

- The generator creates outcomes upfront. The harness must withhold them from
  requests and outcome storage until they become observable. Send confirmation
  requests before advancing the replay cutoff to later outcomes.
- Admit an outcome only when `outcome_available_at_simulated` is **strictly less
  than** `observed_at_simulated`, matching the existing exclusive label-cutoff
  convention. An outcome exactly at the cutoff waits for the next cutoff.
- For an as-of snapshot at simulated time `T`, also require
  `observed_at_simulated <= T`; later ingestion must not silently appear in an
  earlier snapshot. Exact reproduction of what was physically stored requires
  a retained, consistent database snapshot/export; preparation timestamps are not
  proof of commit visibility.
- The current generator has no `cancelled_at`. Do not invent a cancellation at
  confirmation, at the promise, or at a delivery timestamp. Before replaying
  cancellations, agree on an explicit simulated observation policy or separately
  approve a generator change. Until then, retain them in the source; do not claim
  complete terminal-outcome replay or infer cancellation from a missing delivery.

## Joining and checking the records

Join on **both** `run_id` and `order_id`, never on response order, timestamps,
prediction value, or `order_id` alone across experiments. The unique keys prevent
retries from multiplying the scored population. Freeze stored predictions; new
outcomes attach to them without changing what was originally predicted.

For an explicit as-of snapshot, score only successful confirmation-time predictions
with an observed delivered outcome. Check prediction/outcome confirmation times
against the source; do not score a late-created prediction as if it were on time.
Report MAE, bias (predicted minus actual), and P95 absolute
error with their matched count. Report confirmation-window and observation cutoffs
alongside results. Include coverage counts for source orders due for prediction,
successful unique predictions, observed deliveries without a prediction,
predictions awaiting an outcome, and observed cancellations. Pending labels are
not zero errors or known cancellations. Expose incomplete follow-up: early scores
can overrepresent faster deliveries. Future evaluation still needs an agreed,
untouched post-training window; January-August is now training data.

Illustration only, not a measured result: confirmation at 10:00, prediction 43.63
minutes, promise at 10:45, and delivery at 10:50. Before the delivery is observable,
the prediction is pending. At a cutoff after 10:50, the delivered label is 50
minutes, error is -6.37 minutes, and `late_delivery` is true. A retry adds no
second scored order. The actual HTTP latency is independent of those 50 simulated
minutes.

## Implementation acceptance checks, not yet run

Before claiming persistence/replay works, add focused tests for:

1. Stored request/features/output/model hash match the actual API computation;
   outcome fields cannot enter the request or feature snapshot.
2. The same order in different runs stays separate; identical retries, concurrent
   duplicates, and lost-response retries produce one committed prediction.
3. Conflicting requests/models/labels fail explicitly; failed inference or writes
   never produce a fake successful prediction or an unlogged successful response.
4. A restart retains committed records; timeout reconciliation distinguishes
   client observations from durable server state.
5. Labels before/at/after the cutoff, timezone offsets, late ingestion, late-created
   predictions, and deliveries beyond the confirmation window obey the time rules.
6. Null cancellation labels, missing outcomes, failed predictions, and duplicate
   attempts do not distort joins, counts, or errors on a tiny hand-checkable case.

## Accepted decisions and remaining implementation choices

- Daman accepted this contract, including first-write-wins retries and
  failure-on-log-write. Acceptance does not authorize implementation by itself.
- Choose the first local persistence step. PostgreSQL remains the roadmap option;
  no database, driver, volume, or schema migration is installed/created here.
- Agree how run context reaches the logger without changing model features, and
  how operational attempts are recorded. No new endpoint/header is added here.
- Resolve cancellation availability before terminal-outcome replay; choose a
  later evaluation window separately. No evaluation dataset is generated in this step.
