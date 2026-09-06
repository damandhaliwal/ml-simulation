# Demo: putting this ML system in production (10 minutes, all local, $0)

Every claim below resolves to a committed record. Data, artifacts, and the
database stay on your machine; only code, docs, and hashes travel with Git.

## 0. Setup (once)

```sh
uv venv --python 3.13.7 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env && chmod 600 .env   # then set two distinct local passwords
docker compose up --detach --wait postgres
```

Apply `db/migrations/001_app_logging.sql` through `003_attempts.sql` as
`eta_admin` (see `docs/db-eta-app-design.md`); the API never sees the admin
credential. Generate the January–August source or reuse yours.

## 1. Train and evaluate before serving anything

```sh
PYTHONPATH=code .venv/bin/python -W error -m models.lightgbm_eta --segments
PYTHONPATH=code .venv/bin/python -W error -m models.lightgbm_eta --include-test --segments
PYTHONPATH=code .venv/bin/python -W error -m models.late_risk --segments
```

Proves: chronological splits with label cutoffs, three baselines before the
boosted models, test-set opt-in. Record: `docs/evaluation-2026-09-03.md`.

## 2. Freeze and save full-data artifacts

```sh
PYTHONPATH=code .venv/bin/python -W error -m models.refit_eta \
  --data data/orders_2026_jan_aug.json --output-dir artifacts/eta_2026_jan_aug \
  --observed-at 2026-09-03T00:00:00+00:00
PYTHONPATH=code .venv/bin/python -W error -m models.refit_risk \
  --data data/orders_2026_jan_aug.json --output-dir artifacts/risk_2026_jan_aug \
  --observed-at 2026-09-03T00:00:00+00:00
```

Proves: exact tree counts, checksum round-trips, no-overwrite discipline.

## 3. Serve with durable logging

```sh
set -a; source .env; set +a
PYTHONPATH=code .venv/bin/python -m serving.api \
  --model-dir artifacts/eta_2026_jan_aug --risk-model-dir artifacts/risk_2026_jan_aug
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/predict -H 'Content-Type: application/json' \
  --data-binary @docs/prediction-request.example.json
```

Proves: one artifact load at startup (fail-fast), commit-before-response,
identical retries, 409/422/503 semantics, attempt rows for every failure.

## 4. Replay a week and read the report

```sh
PYTHONPATH=code .venv/bin/python -m replay.harness \
  --source data/orders_2026_sep_w1_seed7.json --run-id DEMO \
  --model-dir artifacts/eta_2026_jan_aug
```

Proves: confirmation-order requests, cutoff-gated outcome ingestion,
idempotent reruns, coverage accounting. Held-out record:
`docs/evaluation-2026-09-06.md`.

## 5. Catch a regime shift

```sh
PYTHONPATH=code .venv/bin/python -W error -m monitoring.checks \
  --run-id <october-run> --baseline monitoring/baseline_jan_aug.json
open http://127.0.0.1:8000/dashboard
```

Proves: storm-bias alert fires while overall MAE stays quiet, and input drift
is triaged rather than trusted. Record: `docs/regime-shift-2026-10.md`.

## 6. Run the challenger bout, then the rollback drill

Register both generations (`code/models/registry.py`), replay the untouched
window once per contender, apply the pre-agreed rule, then:

```sh
PYTHONPATH=code .venv/bin/python -c "
from models.registry import promote, rollback
promote('artifacts/registry.json', 'eta-challenger', note='drill')
rollback('artifacts/registry.json', 'eta', note='drill complete')"
```

Proves: promotion archives, rollback restores, serving hashes flip and return.
Record: `docs/evaluation-challenger-2026-09.md`.

## 7. Trust but verify continuously

```sh
PYTHONPATH=code .venv/bin/python -W error -m unittest discover -s tests -q
```

132 green with the stack (106 run / 18 skip without); GitHub Actions repeats
both modes on every push (`.github/workflows/ci.yml`).

## Cleanup

Stop servers with Ctrl+C, delete scratch runs as `eta_admin`, confirm
`0|0|0|0` across the four tables. The demo leaves no residue and no secrets.
