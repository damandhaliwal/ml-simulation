# Marketplace ETA Intelligence System

A learning-first project for predicting food-delivery duration and following
predictions through actual outcomes and the model's operational lifecycle.

**Status:** The order-input generator covers January-August 2026. Delivery dynamics,
batching, trained models, APIs, and deployment are not implemented yet. All
marketplace data is simulated; results describe the simulation, not real deliveries.

## Run this step

Use Python 3.10 or later and the system's IANA timezone database. No Python
packages need to be installed on this Mac. Run from the repository root:

```sh
python3 code/simulator/generate_orders.py
PYTHONPATH=code python3 -m unittest discover -s tests -v
```

The first command generates **116,606 orders** with the default seed and saves
them to `data/orders_2026_jan_aug.json`. Each has an order ID, a UTC confirmation
timestamp, pickup/drop-off zones, and a distance. Rerunning the command replaces
that generated file. `data/` is ignored by Git; commit the generator, not the data.
The dataset is one unsplit file: no train, validation, or test partition is assigned.

The second command runs the focused tests. `PYTHONPATH=code` tells Python where
our source packages live, without installing anything or changing global settings.

Current layout:

```text
code/
    simulator/
        __init__.py
        generate_orders.py
tests/
    test_simulator.py
docs/
    order-schema.md
data/                   # Generated locally; not committed
    orders_2026_jan_aug.json
```

`code/` is a source folder, not a Python package. `simulator/` is the package;
its `__init__.py` only marks that boundary. Add other component folders when
they have actual code, not before.

`generate_orders(start_date, end_date, seed=42)` accepts Python `date` values.
The defaults are January 1 through August 31, 2026, both inclusive in Toronto's
local calendar. The generator stops at September 1 midnight, exclusive.

Generation rules:

- A local random-number generator makes repeated calls with the same arguments
  reproducible without changing Python's global random state.
- Convert the local start and end boundaries to UTC, then advance by independently
  sampled integer gaps of 1-5 minutes until the end boundary. This correctly
  handles the March daylight-saving change. Late-August local orders can have
  September 1 UTC timestamps while still being inside the requested window.
- Arrivals run around the clock with the same gap distribution throughout the
  window. There are no peak-hour, weekday, or seasonal demand effects yet.
- Pick each pickup and drop-off zone independently and uniformly from `Z1`-`Z3`.
- Sample distance uniformly from 0.3-2 km for same-zone orders and 1-5 km for
  cross-zone orders, rounded to two decimals. The ranges overlap deliberately:
  cross-zone orders tend to be longer but need not always be longer.
- IDs start at `O000001` in each batch. They are unique within the batch, not across
  repeated calls; this generator is not an append-only order stream yet.

These are toy simulation assumptions, not estimates of Toronto activity.
The output is only a subset of the agreed schema: restaurant
and customer identities, calendar/weather/traffic fields, courier state, promises,
and outcomes will be added in separate steps. No batching rule is implemented yet.

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
the first order's stops. Account for extra travel and stop time; the numeric
proximity cutoff remains to be chosen. This is an approximation of routing.

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
The promise-setting policy and severe-lateness threshold are still undecided.
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
architecture scaffolding and no large dataset generation at this stage.

### Next decision

Review the date-window generator and its output, then choose the next small
schema addition. Leave train/test splitting for later. Add marketplace state and
mechanisms in separate steps; do not invent placeholder outcomes or independently
randomize shared queues and courier counts. Further implementation requires approval.
