# Marketplace ETA Intelligence System

Current task: **generate synthetic data for predicting delivery duration**.
No queues, courier dispatch, event clock, intermediate stages, or marketplace
state. No trained model, train/test split, API, or deployment yet.

## Two functions

Both live in `code/simulator/generate_orders.py`:

- `generate_order(confirmed_at, ...)` samples one complete row.
- `generate_orders(start_date, end_date, ...)` repeatedly calls it for a date range.

Features are sampled first. A short formula generates delivery duration from
order size, backlog, courier availability, distance, traffic, weather, a random
nearby-batch detour, and noise. All assumptions are synthetic, not estimates of
Toronto operations. See the [schema and formula](docs/order-schema.md).

## Function calls

From the repository root, start Python with `PYTHONPATH=code python3`:

```python
from datetime import date, datetime, timezone
from simulator.generate_orders import generate_order, generate_orders

# Dates belong in the call, not in the function's defaults.
orders = generate_orders(
    date(2026, 1, 1), date(2026, 8, 31),
    seed=42,
    orders_per_hour=20,
    couriers_per_zone=3,
    traffic_index=1.8,
    weather_type="rain",
)

# The same sampler can later supply individual orders for live testing.
order = generate_order(
    datetime.now(timezone.utc),
    order_id="LIVE-001",
    seed=42,
    traffic_index=2,
    weather_type="snow",
)
```

There is no market object to create or advance. Each row already contains its
synthetic outcome. For future live testing, send only inputs to the prediction
API and withhold the outcome until its timestamp; that replay logic is not built.

Useful keyword arguments:

| Controls | Defaults / meaning |
| --- | --- |
| `orders_per_hour`, `couriers_per_zone` | 20 arrivals/hour; 3 sampled local couriers. Rate also scales sampled workload. Zero local couriers is allowed. |
| `traffic_index`, `weather_type`, `temperature_c`, `precipitation_mm_per_hour` | Sample/derive defaults unless overridden. Supply compatible weather when setting temperature or precipitation. |
| `holidays`, `special_events` | Date-to-name dictionaries, empty by default; no official holiday calendar is bundled. |
| `holiday_name`, `special_event` | Override the label for a row. Labels alone do not increase traffic or demand. |
| `item_count`, `prep_time_multiplier` | Sample 1–5 items; preparation multiplier defaults to 1. |
| `promise_minutes`, `cancellation_probability` | A 45-minute promise; a 3% direct cancellation draw. |
| `batch_probability`, `max_orders_per_run`, `batch_max_gap_km` | A 20% chance of extra batch delay; at most 2 orders; pickup and drop-off gaps each at most 1 km. No linked runs are constructed. |
| `zones`, `restaurants_per_zone` | Three synthetic zones; 5 restaurants per zone. |

Extra keyword arguments to `generate_orders` are passed to the single-row
sampler. A call's scenario applies throughout that batch. To switch scenarios,
make new calls with different arguments.

## Run and check

Use Python 3.10+ and the system's IANA timezone database; no third-party packages.

```sh
python3 code/simulator/generate_orders.py \
  --start 2026-01-01 --end 2026-01-01 --output data/orders_simple_sample.json
PYTHONPATH=code python3 -m unittest discover -s tests -v
```

The CLI requires dates and an output path, and replaces that specific output
file if it exists. It also accepts `--seed` and `--orders-per-hour`.
Generated files are ignored by Git.

Dates are inclusive in Toronto-local time; timestamps are stored in UTC.
The window selects **confirmation dates**, not an outcome observation cutoff:
late-window orders can have delivery timestamps beyond the window.

The same seed, order ID, timestamp, and controls reproduce the same row.
Batch IDs are unique within a batch, not across separate batches; pass your own
unique `order_id` for individual calls. Independent calls carry no prior state.

The old `orders_2026_jan_aug.json` and `orders_sample.json` files belong to earlier
generator versions and are left untouched. Regenerate explicitly when ready.

## Prediction contract

- Predict at order confirmation.
- Target: `delivery_duration_minutes`.
- Do not use `status`, `delivered_at`, `delivery_duration_minutes`, or `late_delivery`
  as inference features. Cancelled rows have missing delivery labels.
- IDs are identifiers, not automatically model features.
- The promise is separate from the prediction. Lateness means delivery after
  that original deadline; a point prediction is not a lateness probability.
- Later: agree on a chronological split and establish simple baselines, using
  MAE as the primary metric. No splitting or training happens in this step.

The broader roadmap and working agreement remain in [AGENTS.md](AGENTS.md).
Next: inspect the sampling rules and the delivery-time formula together.
