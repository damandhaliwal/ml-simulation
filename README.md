# Marketplace ETA Intelligence System

A learning-first project for predicting food-delivery duration and following
predictions through actual outcomes and the model's operational lifecycle.

**Status:** A configurable, stateful simulator generates all 37 agreed order
columns, preparation/delivery events, cancellations, and nearby multi-order runs.
No models, train/test split, prediction API, or deployment exists yet.
Everything is synthetic; these are not measurements of Toronto deliveries.

## Run this step

Use Python 3.10+ and the system's IANA timezone database. No additional Python
packages are required. Run from the repository root:

```sh
python3 code/simulator/generate_orders.py \
  --start 2026-01-01 --end 2026-01-01 --output data/orders_sample.json
PYTHONPATH=code python3 -m unittest discover -s tests -v
```

Dates and the output path are required. For January–August, change `--end` to
`2026-08-31`. Dates are inclusive in Toronto's local calendar; confirmation
timestamps are UTC. Labels reflect events observed by midnight after the last
local date. Orders still active then keep missing delivery labels.
The CLI replaces the requested output file. Generated data stays out of Git.

The existing `data/orders_2026_jan_aug.json` is the older five-field dataset;
it is not automatically overwritten by this step.

## Function calls

All simulator functions live in `code/simulator/generate_orders.py`.
For Python examples, launch Python with `PYTHONPATH=code python3`.

```python
from datetime import date, datetime, timedelta, timezone
from simulator.generate_orders import (
    create_market, generate_order, generate_orders, advance_market,
)

market = create_market(
    seed=42,
    couriers_per_zone=3,
    restaurants_per_zone=5,
    max_orders_per_run=2,
    batch_max_gap_km=1.0,
    cancellation_probability=0.03,
    promise_minutes=45,
    holidays={date(2026, 1, 2): "Simulated holiday"},
    special_events={date(2026, 1, 3): "festival"},
)

orders = generate_orders(
    date(2026, 1, 1), date(2026, 1, 1),
    market=market,
    orders_per_hour=20,
    traffic_index=1.8,
    weather_type="rain",
    temperature_c=4,
    precipitation_mm_per_hour=8,
    prep_time_multiplier=1.2,
)
```

This example applies the rain/traffic scenario to one day. Change the end date
to August 31 for a longer cohort after inspecting a small run. Sustained demand
above courier capacity creates growing queues, so long stress runs can be slow.
For varying scenarios, call the single-order function at increasing timestamps
and change its keyword arguments. For example, in a **fresh** market:

```python
market = create_market(seed=42)
now = datetime.now(timezone.utc)   # The caller chooses historical or current time.

snapshot = generate_order(
    now, market, traffic_index=1.8, weather_type="rain", item_count=6,
)
assert snapshot["delivery_duration_minutes"] is None

advance_market(market, now + timedelta(hours=2))
observed_order = market["orders"][snapshot["order_id"]].copy()
```

Advancing time is instant simulated-time processing, not sleeping or calling an
API. `snapshot` remains unchanged even after delivery. Outcomes are read from the
market after advancing. Do not pass the internal market/event queue to a model:
it also holds future simulation events.

To create a same-customer add-on, pass `order_group_id=snapshot["order_group_id"]`
to a subsequent `generate_order` call. It selects another restaurant and retains
the customer/destination. Customer grouping does not guarantee a shared courier.

### Controls and defaults

| Where | Controls |
| --- | --- |
| `create_market` | Seed; zone labels; restaurants/customers/couriers per zone (5/100/3); maximum orders per run (2); proximity cutoff (1 km); cancellation-attempt probability (0.03); promise length (45 minutes); holiday/event calendars. |
| `generate_orders` | Required start/end dates; arrival rate (20 orders/hour); an optional existing market; the same order keyword arguments shown below. Without a market it creates one using `seed`. An existing market keeps its own seed/state. |
| `generate_order` | Required aware timestamp and market; traffic, weather, temperature, precipitation, holiday/event name, preparation multiplier (1), optional item count and existing customer group. |
| `advance_market` | Market and inclusive observation cutoff. Processes only events due by that time. |

- Omitted traffic/weather controls use shared, synthetic hourly conditions.
  Explicit overrides apply from this confirmation until the next generation
  call; omitting them on that next call restores automatic conditions.
- Supply `weather_type` when overriding temperature or precipitation. Contradictory
  combinations fail explicitly. Preparation multiplier applies to the new order.
- Calendar/event labels alone do **not** multiply traffic or demand. Override those
  separately. Calendars default to empty; none is claimed to be an official
  Canadian holiday calendar. Use a calendar for a multi-day long-weekend scenario;
  `holiday_name` only overrides the individual order's date.
- Arrival rate controls frequency, not an individual order. Backlog and idle/busy
  counts come from shared activity, never independent random draws.
- Reuse the market to retain IDs, queues, and couriers; time cannot go backward.
  IDs are unique within a market, not across separately created markets.
  Date-window calls restart their arrival clock at each window boundary, so
  chunking a batch is not identical to one uninterrupted arrival stream.

### Reading the implementation

Start with these four functions:

1. `create_market` builds the world and its policy settings.
2. `generate_order` advances to confirmation, freezes inputs, then starts work.
3. `advance_market` processes preparation, cancellation, arrival, and stop events.
4. `generate_orders` repeatedly calls the single-order function for a date window.

The helpers each support one responsibility:

| Helpers | Purpose |
| --- | --- |
| `_number`, `_utc` | Validate numeric controls and normalize aware timestamps to UTC. |
| `_conditions`, `_calendar` | Build current environmental and local calendar fields. |
| `_schedule` | Put an event on the time-ordered queue, breaking ties by insertion order. |
| `_start_prep` | Start the next active order when a restaurant's preparation slot is free. |
| `_gap` | Sample once and retain a symmetric pickup or drop-off proximity value. |
| `_record_plan` | Preserve a timestamped stop-plan revision after adding/removing an order. |
| `_dispatch` | Assign waiting orders to an eligible batch or an idle courier. |
| `_travel_next`, `_service_stop` | Schedule travel and one minute of stop handling. |
| `_cancel` | Cancel only before pickup, release preparation/assignment, and preserve observed milestones. |

See the [order schema](docs/order-schema.md#current-simulator-rules) for formulas,
assumptions, and limitations. Runs and assignments stay separately in
`market["runs"]` and `market["assignments"]`; the returned list/CLI JSON contains
order rows only. The market is in-memory, not a restartable database or live service.

Source stays under `code/simulator/`, tests under `tests/`, and design notes under
`docs/`. `code/` is not a package; `PYTHONPATH=code` exposes the simulator package.

## Product spec

### Who this serves and why

A customer needs a useful delivery estimate when an order is confirmed.
Underestimation creates disappointment; systematic overestimation may discourage
orders. A marketplace operator needs to know when estimates become unreliable.

### The simulated market

One dense downtown market inspired by Toronto: many restaurants and orders,
multiple pickup/drop-off zones, and deliveries that can cross zone boundaries.
This is a fictional market, not a reconstruction of Toronto's streets, actual
restaurant activity, or any platform's proprietary system.

Use synthetic zone labels and randomly generated distances, with shorter trips
more common within a zone. No coordinates, map sketches, or route optimization
are required. Only batch additional orders whose pickups and drop-offs are near
the first order's stops. Account for extra travel and stop time; the default
proximity cutoff is 1 km at both ends. This is an approximation of routing.

The design includes weather, traffic, local time and weekday, holidays and special
events, promised deadlines, cancellations, and multi-order courier runs. Related
orders from one customer and orders sharing a courier run are separate concepts.
See the [order schema](docs/order-schema.md) for fields and relationships.

### Prediction contract

- **When:** Once, at order confirmation, before preparation and delivery finish.
- **Target:** `delivery_duration_minutes` = minutes from confirmation to delivery.
- **Initial output:** A predicted duration in minutes, not an arrival timestamp.
- **Later output:** Late-delivery probability, model version, prediction ID,
  and measured prediction latency.
- **Label availability:** The actual duration becomes available only when the
  delivery finishes, even if the simulator knows the outcome internally earlier.
- **Cancellations:** Retain cancelled orders, but leave their delivery duration
  and delivery-lateness label missing. Report cancellation rates separately.

Inputs include restaurant, location, distance, confirmation time,
weather, restaurant backlog, basket details, traffic, and courier supply/demand.
Freeze these as a confirmation-time snapshot. Every feature must be available
then; historical summaries may use only information already observed. Actual
preparation, courier-wait, travel times, and future courier assignments are not inputs.

### ETA error is different from lateness

An order confirmed at 18:00 and delivered at 18:38 has a duration of 38 minutes.
A prediction of 35 minutes has an absolute error of 3 minutes and a signed error
of -3 minutes. If the promised deadline was 18:40, the delivery was not late.

Record `promised_delivery_at` at confirmation and preserve that original promise.
For delivered orders, define `late_delivery` as delivery after that deadline.
The current promise is a configurable 45 minutes. A severe-lateness threshold
has not been selected.
A point ETA alone does not provide a late-delivery probability.

### How we will evaluate it

- **Primary:** MAE, the mean absolute prediction error in minutes.
- **Secondary:** RMSE, median and 90th-percentile absolute error, and bias
  defined as mean(predicted - actual). Negative bias means underprediction.
- **Operational relevance:** Share of orders with absolute error below 5 and
  10 minutes, plus the underprediction rate. Add severe-lateness and classifier
  metrics when their definitions and the risk model are agreed.
- **Coverage:** ETA error describes delivered orders only. Show delivered,
  cancelled, and still-active order counts alongside it; do not silently drop
  cancellations or treat unfinished orders as completed observations.
- **Validation:** Train on earlier orders, validate on later orders, and reserve
  the latest test period. Respect delayed label availability and never tune on
  the final test set. Compare with simple baselines and inspect relevant segments.

The 5- and 10-minute bands are reporting measures, not launch targets. No model
accuracy, latency, or promotion threshold has been chosen or achieved yet.

### Scope and first milestone

Start offline with a small, seeded, inspectable simulation. Keep all confirmed
orders, including cancellations, and learn from observed deliveries. Introduce
the agreed marketplace mechanisms one at a time, then establish simple baselines
and a chronological model comparison. Rich domain scope does not require a
large dataset or infrastructure at the first step.

Eventually, serve predictions through an API, store predictions and delayed
outcomes, monitor errors and drift, and evaluate new models before promotion
with rollback available. The staged roadmap is in [AGENTS.md](AGENTS.md).

Not now: cloud infrastructure, dashboards, automatic retraining, sophisticated
dispatch optimization, courier incentives, or causal experiments. Simple courier
assignment and batching rules are part of the simulator design. No full
architecture scaffolding.

### Next decision

Review one order's confirmation snapshot and event history, then compare a small
normal scenario with a high-traffic or low-supply scenario. Leave train/test
splitting and modeling for later. Further implementation requires approval.
