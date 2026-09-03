# Offline ETA evaluation — September 3, 2026

## Assessment

Usable for this synthetic learning prototype, with caveats; no production
acceptance threshold has been agreed. LightGBM's August MAE is 3.196 minutes
versus 3.420 for linear regression (about 6.5% lower). Overall P95 absolute error
is slightly lower, but mean underprediction and some segment tail errors are
worse. No model settings were changed in response to August scores. Full-data
refitting is pending Daman's acceptance of these tradeoffs.

## Source and evaluation contract

- Source: `data/orders_2026_jan_aug.json`, generated with seed 42 and 20 orders/hour.
- SHA-256: `be32c722f37cbe6a80ce25a45de70765765b512fd8309e57ace7b2c28d9d5666`.
- Population: 116,667 synthetic orders, 113,079 delivered and 3,588 cancelled.
  Duration errors condition on observed delivery; cancellation rate is 3.08%.
- Confirmation windows, in America/Toronto: January–June training, July
  validation, August test.
- Training labels must arrive strictly before July 1 at 00:00 EDT; validation
  labels strictly before August 1 at 00:00 EDT. This excludes 16 and 12 rows,
  respectively. They remain in the source and are returned separately.
- Eligible counts: 84,104 train, 14,466 validation, 14,481 test. These total 113,051;
  adding the 28 excluded labels recovers all 113,079 delivered orders.
- Test follows August confirmations to final delivery; the last outcome arrives
  September 1 at 00:42:27.651978 EDT. There is no midnight September 1 censoring.
- Prediction moment: confirmation. Outcome columns are excluded from features.
  No historical aggregates or learned preprocessing were introduced.
- Baselines fit training only. LightGBM uses L1 loss, maximum 300 trees,
  learning rate 0.05, 31 leaves, minimum 20 samples/leaf, seed 42, and
  25-round validation early stopping. July selects **160 trees**. Training
  data is not expanded before August scoring.
- The linear model uses seven numerical inputs; LightGBM uses thirteen inputs,
  including additional calendar, temperature, and categorical features.
  This compares complete predictors, not the isolated effect of algorithm choice.
- MAE = mean absolute error; bias = mean(predicted − actual); P95 uses NumPy's
  default linear percentile interpolation on absolute errors; RMSE is the square
  root of mean squared error. Units are minutes. Predictions retain the existing
  five-minute floor and two-decimal rounding. Metrics are reported to three decimals.
- Evaluation functions require prepared eligible splits. CLI preparation enforces
  cutoffs. `include_test=False` is the function default; `--include-test` is an
  explicit CLI opt-in, not a persistent lock or proof a holdout was never viewed.
- Earlier scripts printed August scores by default. Prior viewing/tuning history
  is not established. This is the existing holdout, not a newly untouched dataset.
  This session evaluated it with frozen settings and reproduced the same results
  in a fresh environment without tuning.

## Overall results

| Model | Split | n | MAE | Bias | P95 absolute error | RMSE |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Global Mean | train | 84,104 | 7.533 | 0.000 | 18.261 | 9.498 |
| Global Mean | val | 14,466 | 7.433 | 0.373 | 17.821 | 9.332 |
| Global Mean | test | 14,481 | 7.414 | 0.143 | 17.799 | 9.278 |
| Domain Heuristic | train | 84,104 | 8.461 | -8.162 | 18.620 | 10.130 |
| Domain Heuristic | val | 14,466 | 8.226 | -7.919 | 18.210 | 9.862 |
| Domain Heuristic | test | 14,481 | 8.325 | -8.032 | 18.010 | 9.904 |
| Linear Regression | train | 84,104 | 3.535 | 0.000 | 9.010 | 4.555 |
| Linear Regression | val | 14,466 | 3.485 | 0.300 | 8.617 | 4.439 |
| Linear Regression | test | 14,481 | 3.420 | 0.254 | 8.650 | 4.351 |
| LightGBM | train | 84,104 | 3.204 | -0.507 | 8.670 | 4.223 |
| LightGBM | val | 14,466 | 3.240 | -0.369 | 8.620 | 4.221 |
| LightGBM | test | 14,481 | 3.196 | -0.442 | 8.470 | 4.163 |

## August segment checks

The tables compare the two learned models on identical August orders. Each
dimension separately sums to 14,481; do not sum across dimensions. Distances are
≤2 km, >2–4 km, and >4 km. Hours are Toronto-local clock hours. All group definitions
use confirmation-time inputs, not outcomes.

Only observed groups are shown. There is no snow in July or August; performance
in snow is **not evaluated**. Idle-courier counts are only 1 or 2, so zero-courier
and other supply regimes are also not evaluated.

Storm MAE improves from 4.684 to 3.516 minutes, but LightGBM still underpredicts
storms by 0.892 minutes on average. At 08:00, LightGBM P95 is 11.255 versus 10.634
for linear regression; at 18:00 it is 10.607 versus 10.237. Lower overall MAE does
not mean uniformly better tails. These are descriptive checks, not significance
tests, business pass/fail gates, or reasons to tune against August.

### weather_type

| Segment | n | Linear MAE | LightGBM MAE | LightGBM bias | Linear P95 | LightGBM P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| clear | 10,070 | 3.293 | 3.135 | -0.352 | 8.340 | 8.310 |
| rain | 3,691 | 3.521 | 3.301 | -0.602 | 8.710 | 8.890 |
| storm | 720 | 4.684 | 3.516 | -0.892 | 11.472 | 10.411 |

### pickup_zone_id

| Segment | n | Linear MAE | LightGBM MAE | LightGBM bias | Linear P95 | LightGBM P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Z1 | 4,732 | 3.484 | 3.243 | -0.394 | 8.890 | 8.694 |
| Z2 | 4,913 | 3.360 | 3.154 | -0.435 | 8.494 | 8.254 |
| Z3 | 4,836 | 3.419 | 3.192 | -0.496 | 8.600 | 8.455 |

### distance_band

| Segment | n | Linear MAE | LightGBM MAE | LightGBM bias | Linear P95 | LightGBM P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0-2 km | 7,209 | 3.407 | 3.164 | -0.343 | 8.696 | 8.470 |
| 2-4 km | 4,855 | 3.303 | 3.213 | -0.530 | 8.340 | 8.500 |
| >4 km | 2,417 | 3.698 | 3.257 | -0.561 | 9.082 | 8.424 |

### idle_couriers

| Segment | n | Linear MAE | LightGBM MAE | LightGBM bias | Linear P95 | LightGBM P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 7,272 | 3.418 | 3.171 | -0.477 | 8.620 | 8.450 |
| 2 | 7,209 | 3.423 | 3.221 | -0.407 | 8.680 | 8.500 |

### local_hour

| Segment | n | Linear MAE | LightGBM MAE | LightGBM bias | Linear P95 | LightGBM P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 00 | 605 | 3.269 | 3.114 | -0.195 | 8.400 | 8.060 |
| 01 | 609 | 3.130 | 2.974 | -0.008 | 7.878 | 7.470 |
| 02 | 602 | 3.225 | 3.051 | -0.002 | 8.387 | 7.974 |
| 03 | 596 | 3.316 | 3.117 | -0.248 | 7.992 | 7.783 |
| 04 | 598 | 3.360 | 3.226 | -0.828 | 8.003 | 8.494 |
| 05 | 605 | 3.210 | 3.045 | -0.533 | 8.398 | 8.030 |
| 06 | 592 | 3.179 | 3.006 | -0.203 | 8.213 | 8.198 |
| 07 | 605 | 4.038 | 3.569 | -1.035 | 10.376 | 10.022 |
| 08 | 598 | 4.239 | 3.714 | -0.860 | 10.634 | 11.255 |
| 09 | 610 | 3.214 | 3.076 | -0.402 | 7.957 | 7.685 |
| 10 | 605 | 3.159 | 3.054 | -0.312 | 8.092 | 8.270 |
| 11 | 609 | 3.296 | 3.311 | -0.285 | 7.922 | 7.880 |
| 12 | 602 | 3.382 | 3.262 | -0.144 | 8.487 | 8.127 |
| 13 | 595 | 3.077 | 3.030 | -0.052 | 7.522 | 7.433 |
| 14 | 583 | 3.182 | 3.102 | -0.532 | 7.720 | 7.935 |
| 15 | 633 | 3.278 | 3.124 | -0.535 | 8.454 | 8.236 |
| 16 | 641 | 4.214 | 3.604 | -0.929 | 10.510 | 10.260 |
| 17 | 596 | 4.003 | 3.277 | -0.405 | 9.147 | 8.895 |
| 18 | 603 | 4.157 | 3.588 | -1.023 | 10.237 | 10.607 |
| 19 | 599 | 3.377 | 3.324 | -0.434 | 8.313 | 8.417 |
| 20 | 615 | 3.446 | 3.236 | -0.518 | 8.742 | 9.041 |
| 21 | 593 | 3.002 | 2.924 | -0.518 | 7.648 | 7.540 |
| 22 | 581 | 2.988 | 2.844 | -0.252 | 7.150 | 7.270 |
| 23 | 606 | 3.284 | 3.091 | -0.321 | 8.120 | 8.153 |

## Verification and reproduction

Tested on CPython 3.13.7, macOS arm64, with the exact Python package versions in
`requirements.txt` and the host's existing OpenMP runtime.

```sh
PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -v
PYTHONPATH=code .venv/bin/python -W error -m prep.dataset_validation
PYTHONPATH=code .venv/bin/python -W error -m models.baselines --segments
PYTHONPATH=code .venv/bin/python -W error -m models.lightgbm_eta --segments
# Explicit frozen-model audit; do not use these scores to tune:
PYTHONPATH=code .venv/bin/python -W error -m models.lightgbm_eta --include-test --segments
git diff --check a0b2601
shasum -a 256 data/orders_2026_jan_aug.json
```

- 36 unit tests passed with warnings treated as errors in both the existing
  project environment and a fresh isolated environment installed from
  `requirements.txt`; all eight installed packages passed `uv pip check`.
- The two default modeling CLIs printed validation segments and no test scores
  or warnings. The frozen evaluation reproduced 160 trees and the same August
  LightGBM scores in the fresh environment.
- MAE, bias, P95 interpolation, and RMSE were recomputed independently from
  LightGBM's August predictions and matched the reported figures.
- All model validation/test segment counts and count-weighted segment MAEs
  reconcile to their overall results (allowing 0.001 minute for displayed rounding).
- Dataset counts and checksum reconcile; raw data and simulator are unchanged.
- Focused tests cover label boundaries (including exact equality), explicit
  timezones, timestamp/target agreement, duplicates before filtering,
  test opt-in, unchanged fit under altered test labels, no-outcome inference,
  early stopping, and segment counts/calculations.
- No full-data model, model artifact, prediction API, or deployment was created.
  The temporary verification environment is local under
  `/private/tmp/eta-review-env.xmKqa0`; it is not required by the project.

## Next decision

If Daman accepts the prototype errors, agree on one separate full-data refit:
use all 113,079 delivered rows after every label is available, retain the frozen
feature mappings/hyperparameters, and fit exactly 160 trees without reusing August
for early stopping. Preserve this evaluation record. Save and round-trip-check the
model plus feature/schema and training-window metadata in the approved next step.
A future untouched confirmation window is required to evaluate the refitted model;
in-sample January–August errors would not establish generalization.

For code revision/provenance, use the commit containing this record:
`git log -1 -- docs/evaluation-2026-09-03.md`. See [handoff.md](handoff.md).
