# Marketplace ETA Intelligence System

[![CI](https://github.com/damandhaliwal/ml-simulation/actions/workflows/ci.yml/badge.svg)](https://github.com/damandhaliwal/ml-simulation/actions/workflows/ci.yml)

Predict food-delivery time at order confirmation, estimate late-delivery risk, and operate both models through a complete local MLOps loop — training, serving, logging, monitoring, retraining, and rollback.

> All data, metrics, and predictions in this repository are **fully synthetic**. Results demonstrate engineering practice, not real-world delivery accuracy.

## What this is

A self-contained, $0-cost reference system for production-style ML on a synthetic Toronto-inspired delivery market:

- **ETA prediction** at confirmation time (`delivery_duration_minutes`)
- **Late-delivery risk** scoring against the promised deadline
- **Local serving** via validated Python/CLI and FastAPI, with Docker packaging
- **Operational loop** with PostgreSQL logging, live replay, drift/performance monitoring, challenger evaluation, and versioned promotion/rollback

No cloud resources, no paid services, no real customer data. Everything runs on your machine.

## Why this project exists

I built this to show I can take ML to production, not just train models in notebooks. A consistent piece of feedback from recruiters was to demonstrate deployment and operations — serving, logging, monitoring, retraining, and rollback — so this project answers that directly with a complete, runnable system.

## Key capabilities

- **Inspectable synthetic generator** — three zones, weather, traffic, calendar effects, cancellations, and nearby multi-order batching via an explicit formula. See `docs/order-schema.md`.
- **Leakage-safe evaluation** — chronological splits with label-availability cutoffs, three baselines before LightGBM, test-set opt-in guard.
- **Reproducible artifacts** — full-data refits with checksums, metadata sidecars, and exact save/load verification. Artifacts stay local and Git-ignored.
- **Validated prediction interface** — strict 13-field contract shared by Python, CLI, and HTTP. No coercion, imputation, or silent fallback.
- **FastAPI service** — `/health`, `/predict`, and `/dashboard` on localhost, single artifact load at startup, fail-fast on bad artifacts.
- **Durable logging** — PostgreSQL-backed predictions, outcomes, and operational attempts with least-privilege access and idempotent retries.
- **Live replay** — confirmation-order requests through the real API with cutoff-gated outcome ingestion and matched-pair scoring.
- **Monitoring** — performance alerts, input-drift checks, and an HTML dashboard.
- **Model management** — registry with challenger evaluation on untouched windows and explicit promotion/rollback.
- **CI** — unit suite plus full integration suite against PostgreSQL 17 on every push.

## Architecture

```text
Simulator → Chronological splits → Baselines / LightGBM → Versioned artifacts
                                                              ↓
Live replay → FastAPI (/predict) → PostgreSQL (predictions/outcomes/attempts)
                  ↓                                              ↓
            ETA + risk responses                      Monitoring / Challenger eval
```

- Training is offline; serving loads one explicitly selected artifact at startup.
- Predictions are immutable; outcomes join later after delivery.
- Simulated time (order timestamps) is separate from wall-clock serving latency.

## Requirements

- Python 3.13.7, macOS arm64 tested (Linux ARM64 via Docker)
- Docker Desktop (for API image and PostgreSQL)
- `libomp` on macOS for LightGBM (`brew install libomp` if imports fail)
- No cloud account or paid dependency

## Quickstart

```sh
# 1. Environment
uv venv --python 3.13.7 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# 2. Generate synthetic data
.venv/bin/python code/simulator/generate_orders.py \
  --start 2026-01-01 --end 2026-08-31 --seed 42 --orders-per-hour 20 \
  --output data/orders_2026_jan_aug.json

# 3. Evaluate (train/validation by default; test requires explicit opt-in)
PYTHONPATH=code .venv/bin/python -W error -m models.lightgbm_eta --segments
PYTHONPATH=code .venv/bin/python -W error -m models.lightgbm_eta --include-test --segments
PYTHONPATH=code .venv/bin/python -W error -m models.late_risk --segments

# 4. Freeze full-data artifacts
PYTHONPATH=code .venv/bin/python -W error -m models.refit_eta \
  --data data/orders_2026_jan_aug.json \
  --output-dir artifacts/eta_2026_jan_aug \
  --observed-at 2026-09-03T00:00:00Z
PYTHONPATH=code .venv/bin/python -W error -m models.refit_risk \
  --data data/orders_2026_jan_aug.json \
  --output-dir artifacts/risk_2026_jan_aug \
  --observed-at 2026-09-03T00:00:00Z

# 5. Serve locally
PYTHONPATH=code .venv/bin/python -W error -m serving.api \
  --model-dir artifacts/eta_2026_jan_aug \
  --risk-model-dir artifacts/risk_2026_jan_aug --port 8000

curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/predict \
  -H 'Content-Type: application/json' \
  --data-binary @docs/prediction-request.example.json
```

For the full 10-minute end-to-end story (training → serving → replay → monitoring → challenger → rollback), see `docs/demo.md`.

## API reference

Base URL (local only): `http://127.0.0.1:8000`

| Endpoint | Description |
| --- | --- |
| `GET /health` | Readiness, loaded `model_sha256`, `simulated: true`. Loaded means artifact checks passed, not that accuracy is acceptable. |
| `POST /predict` | One JSON object in, one ETA (+ optional `late_probability`) out. `200` on success, `422` on invalid input, `503` when not ready. |
| `GET /dashboard` | Plain-HTML per-run cards (matched count, MAE, bias, P95, alerts). Requires database; `503` otherwise. |

Example response for `docs/prediction-request.example.json`:

```json
{
  "order_id": "EXAMPLE-001",
  "predicted_delivery_duration_minutes": 43.63,
  "model_sha256": "29447c8ee3ac6ac62d0f72b61d43f24668d01ed62b7974266b9f7991d3ca5dcd",
  "simulated": true
}
```

The request contract is 13 fields (`order_id`, `confirmed_at`, distance, counts, weather, zones — see the example file). Unknown categories, numeric strings, missing/extra fields, and outcome fields are rejected. The API binds to `127.0.0.1` by default and has no authentication, TLS, or rate limiting — it is a local learning service, not a public deployment.

Optional durable logging: send `X-Run-Id` + `X-Predicted-At` headers together. The run must already be registered; the row commits before the response so retries are byte-identical. Requires `POSTGRES_DB`, `POSTGRES_APP_USER`, and `POSTGRES_APP_PASSWORD` (`PGHOST` defaults to `127.0.0.1`).

## Data and modeling

- **Target:** `delivery_duration_minutes`, measured from order confirmation to delivery. Cancelled orders have no label and are excluded from error metrics.
- **Promise vs. prediction:** the 45-minute promised deadline is separate from the ETA. Lateness is delivery after that deadline; a point ETA is not a lateness probability.
- **Features:** only information available at confirmation time. Actual preparation, wait, and travel durations are outcomes, never inputs.
- **Splits:** Toronto-local confirmation dates (Jan–Jun train, Jul validation, Aug test) with strict delivery-observation cutoffs. Late-arriving labels are tracked separately, never silently moved.
- **Models:** global-mean, heuristic, and linear baselines; LightGBM ETA regressor (L1 loss) and LightGBM late-risk classifier on the same 13 features.
- **Metrics:** MAE (primary), bias, P95 absolute error, RMSE; log-loss / Brier / AUC for risk. Segmented by weather, zone, hour, distance, and courier availability.

Detailed setup and held-out records: `docs/evaluation-2026-09-03.md`, `docs/evaluation-2026-09-06.md`, `docs/evaluation-challenger-2026-09.md`.

## Operations

### Docker

The image contains Python 3.13.7, pinned requirements, and API code only — never the model. Mount your own trusted artifact read-only:

```sh
docker build -t eta-api:local .
docker run --rm --name eta-api \
  -p 127.0.0.1:8000:8000 \
  --mount type=bind,source=./artifacts/eta_2026_jan_aug,target=/model,readonly \
  eta-api:local
```

### PostgreSQL

Pinned `postgres:17.11-bookworm` via Compose, localhost-only (`127.0.0.1:5432`), named volume, least-privilege `eta_app` login for the API:

```sh
cp .env.example .env && chmod 600 .env
docker compose up --detach --wait postgres
```

Migrations live in `db/migrations/` (`001_app_logging`, `002_risk_logging`, `003_attempts`). Design notes: `docs/db-eta-app-design.md`, `docs/prediction-logging.md`.

### Replay and monitoring

```sh
# Replay a window through the running API
PYTHONPATH=code .venv/bin/python -m replay.harness \
  --source data/orders_2026_jan_aug.json --run-id REPLAY-EXAMPLE \
  --start 2026-08-04 --end 2026-08-04 --model-dir artifacts/eta_2026_jan_aug

# Check performance and drift for a logged run
PYTHONPATH=code .venv/bin/python -W error -m monitoring.checks \
  --run-id REPLAY-EXAMPLE --baseline monitoring/baseline_jan_aug.json
```

Regime-shift case study: `docs/regime-shift-2026-10.md`.

## Project structure

```text
code/
  simulator/     synthetic order generator
  prep/          validation and dataset preparation
  models/        baselines, LightGBM ETA/risk, refit, predict interface, registry
  serving/       FastAPI app and dashboard
  persistence/   Postgres config, prediction/outcome logging
  replay/        live replay harness through the API
  monitoring/    performance and drift checks
tests/           focused unit + integration tests (DB-gated where needed)
db/migrations/   versioned Postgres schema
docs/            schema, evaluations, design notes, 10-minute demo
monitoring/      checked-in baseline for drift comparison
Dockerfile       local API image (model mounted, not baked in)
compose.yaml     local PostgreSQL service
```

## Testing and CI

```sh
PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -v
```

- 132 tests pass with the local database; 106 run / 18 skip without it.
- GitHub Actions (`.github/workflows/ci.yml`) runs both modes on every push: unit suite plus full suite against a PostgreSQL 17 service with migrations applied.

## Documentation

- `docs/demo.md` — 10-minute runnable tour of the whole system
- `docs/order-schema.md` — generator columns and delivery-time formula
- `docs/prediction-request.example.json` — canonical `/predict` request
- `docs/prediction-logging.md` — prediction/outcome logging contract
- `docs/db-eta-app-design.md` — roles, ownership, and grants
- `docs/evaluation-2026-09-03.md` / `docs/evaluation-2026-09-06.md` — training and held-out results
- `docs/regime-shift-2026-10.md` — monitoring case study
- `docs/evaluation-challenger-2026-09.md` — challenger bout and rollback drill

## Limitations and non-goals

- Synthetic data only; not calibrated to any real city, restaurant, or courier fleet.
- Localhost service without auth, TLS, rate limiting, or load testing.
- No streaming infrastructure (Kafka, Airflow, feature stores) — persistence is plain PostgreSQL + versioned SQL migrations.
- No neural networks or LLMs; boosted trees are sufficient for the tabular task.
- Cloud deployment deferred under the $0 budget.
