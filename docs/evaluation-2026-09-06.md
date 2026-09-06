# Held-out ETA evaluation — September 6, 2026

First score for the full-data artifact on data it never trained on. All data
and errors are simulated; this is not real-world accuracy evidence.

## Source and model

- Source: `data/orders_2026_sep_w1_seed7.json` (local, Git-ignored), generated
  September 6 with seed 7 at 20 orders/hour for Toronto dates September 1–7.
  SHA-256: `f144719507fdb4eb318be226dc2d11edb4584ac1efe5d5fc3df7c68284371b03`.
- Population: 3,407 orders — 3,311 delivered, 96 cancelled (2.82%).
- Model: the frozen full-data artifact, 160 trees on all 113,079 January–August
  labels. Model SHA-256 `29447c8ee3ac6ac62d0f72b61d43f24668d01ed62b7974266b9f7991d3ca5dcd`
  verified at server health before the run and unchanged after. No setting was
  touched; viewing these scores forbids tuning against them.
- Run: `EVAL-SEPW1-SEED7` through the live local API, one confirmation-time
  request per order, outcomes admitted only after availability, final sweep for
  slow deliveries. Rerunning the run ID reproduced the report byte-identically.

## Coverage

3,407 predictions stored, 3,311 matched deliveries, 0 pending, 96 predictions
for cancelled orders (counted, never ingested), 0 observed deliveries without
a prediction, 96 observed cancellations. Observation cutoff
`2026-09-08T04:59:00.513054+00:00` — past the window end, as slow deliveries
require. Metrics use the shared helper (MAE primary; bias is predicted minus
actual; minutes).

## Results

| Population | n | MAE | Bias | P95 absolute error | RMSE |
| --- | ---: | ---: | ---: | ---: | ---: |
| September week, full-data model | 3,311 | 3.320 | -0.568 | 9.120 | 4.372 |

Weather cuts (confirmation-time inputs, same stored rows):

| Segment | n | MAE | Bias | P95 absolute error | RMSE |
| --- | ---: | ---: | ---: | ---: | ---: |
| clear | 2,329 | 3.223 | -0.408 | 8.522 | 4.196 |
| rain | 840 | 3.510 | -0.894 | 10.007 | 4.658 |
| storm | 142 | 3.789 | -1.261 | 11.969 | 5.334 |

Storm underprediction and tail errors remain the weakest slice, consistent
with the August pre-refit read. Do not compare 3.320 against August's 3.196:
different model, different data — no improvement or regression claim follows.

## Verification and reproduction

```sh
shasum -a 256 data/orders_2026_sep_w1_seed7.json artifacts/eta_2026_jan_aug/model.joblib
PYTHONPATH=code .venv/bin/python -m replay.harness --source data/orders_2026_sep_w1_seed7.json \
  --run-id EVAL-SEPW1-SEED7 --model-dir artifacts/eta_2026_jan_aug
```

- Rerun reproduced the report byte-identically (idempotent resume, no duplicates).
- All four metrics were recomputed independently from stored rows and matched.
- The run rows were admin-deleted after recording; residue `0|0|0`.
- Full suite: 95 pass with the database, 82 run / 14 skip without.

## Next decisions

Cancellation timing is coverage-only by decision, so terminal-outcome replay
remains delivered-only. The rest of September is untouched and available for
later windows. See [handoff.md](handoff.md).
