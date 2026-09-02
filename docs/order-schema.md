# Order schema

The simulator implements all 37 order columns below, with related runs,
assignments, and stop-plan revisions held separately in the market dictionary.
This is an in-memory simulator, not a database migration or production platform.
Reassignment is not simulated; an assignment ends on delivery or cancellation.

## Market boundary

- One synthetic, busy downtown market inspired by Toronto, with many restaurants
  and orders spread across multiple zones. Cross-zone deliveries are supported.
- Zones share one city-wide weather/traffic context and the same initial population
  counts. Their queues and courier supply subsequently differ through activity.
  Separate zone-specific demand/environment controls remain future extensions.
- Use synthetic identifiers and zone labels, not real customers or restaurant data.
  No coordinate system, map sketch, street network, or shortest-path calculation
  is required for this simulation.
- Store timezone-aware timestamps in UTC. Derive local calendar features using
  `America/Toronto`, including daylight-saving changes.
- Three zone labels are the default, but callers can supply others. Dates are
  required arguments, never fixed defaults. January–August 2026 remains a caller-
  selected historical window. The data is unsplit. See the
  [README](../README.md#function-calls) for historical and single-order calls.

## What one row means

One order is one customer's purchase from one restaurant. It remains one order
if its courier changes or it joins a multi-order delivery run.

| Record | Meaning and relationship |
| --- | --- |
| Service area / zone | One downtown market contains multiple zones. A restaurant or customer location belongs to a zone. |
| Customer order group | Related purchases for one customer. An ordinary order is a group of one; an add-on can join the group later. |
| Order | One restaurant purchase, linked to one customer order group. |
| Delivery run | Work assigned to one courier; it can contain orders from different customers or restaurants. |
| Run assignment | Links an order to a run, recording assignment and removal times so reassignment does not erase history. |
| Run stop | One planned pickup or drop-off within a run, with its sequence and eventual outcome. |

Customer grouping does not guarantee a shared courier. Sharing a courier does
not imply a shared customer. Keep both relationships separate.

For example, customer C1 orders from R1 and adds an order from R2. These are two
order rows in the same customer group. A courier might carry both plus C2's
order from R1: three orders, two customer groups, one delivery run.

## Order identity and location

IDs are synthetic strings used for joins and tracking, not automatically model
features. The first ETA model does not need customer identity as a predictor.

| Field | Type | Definition |
| --- | --- | --- |
| `order_id` | String | Unique merchant-order identifier. |
| `order_group_id` | String | Customer group this order belongs to; future group members are not known at its confirmation. |
| `customer_id` | String | Synthetic customer placing the order. Group members must share this customer. |
| `restaurant_id` | String | Restaurant preparing this order. |
| `service_area_id` | String | The synthetic downtown market. |
| `pickup_zone_id` | String | Zone containing the restaurant. |
| `dropoff_zone_id` | String | Zone containing the customer destination. |
| `distance_km` | Positive number | Sampled restaurant-to-customer travel distance, before any batching detours; not a measured map route. |
| `confirmed_at` | Timestamp | Prediction time and the start of delivery duration. |
| `promised_delivery_at` | Timestamp | Original deadline communicated at confirmation; must be after confirmation. |

Restaurant and customer identities retain consistent zone labels across
records. The generator samples positive
travel distances, with shorter distances more common within a zone than across
zones. These values approximate travel, not a geometric street map.

## Confirmation-time context

Freeze this snapshot before the new order changes queues or courier assignments.
Do not overwrite it as the marketplace evolves.

| Field | Type | Definition |
| --- | --- | --- |
| `restaurant_backlog` | Nonnegative integer | Existing orders awaiting or undergoing preparation at this restaurant; excludes this order and food already ready for pickup. |
| `idle_couriers` | Nonnegative integer | Online, unassigned couriers currently in the pickup zone. |
| `busy_couriers` | Nonnegative integer | Couriers in the pickup zone occupied by a run, including finishing an already-started leg after cancellation. Some may be eligible for another order. |
| `orders_waiting_for_courier` | Nonnegative integer | Existing active orders with pickup in this zone and no current courier assignment. |
| `basket_value_cad` | Positive number | Restaurant item subtotal in synthetic Canadian dollars, before tax, tips, and delivery fees. |
| `item_count` | Positive integer | Number of items in this restaurant order. |
| `traffic_index` | Positive number | Observed route-context multiplier relative to baseline road travel time: 1.0 is baseline, 1.5 means 50% longer road travel under those conditions. |
| `weather_type` | Category | Clear, rain, snow, or storm (heavy rain in this toy model). |
| `temperature_c` | Number | Observed temperature in degrees Celsius. |
| `precipitation_mm_per_hour` | Nonnegative number | Observed precipitation intensity, using water-equivalent millimetres per hour. |

Traffic affects road travel, not the whole delivery duration. The traffic index
must not be calculated from this order's eventual travel time. Weather and
traffic are snapshots, not perfect knowledge of conditions later in the journey.
This version uses one shared city-wide context. A courier retains its departure
zone until arriving in the next stop's zone; this is a coarse location model.

Zero idle couriers is valid. Couriers may finish another delivery, travel from
another zone, or accept an additional order. The order may wait or be cancelled
under the agreed policy. Do not replace zero with one to avoid handling it.

## Derived calendar fields

These can appear as columns in the model dataset, but are derived consistently
from the local confirmation time and a configured calendar, not drawn randomly.

| Field | Type | Source |
| --- | --- | --- |
| `local_hour` | Integer, 0-23 | Confirmation timestamp in the market's timezone. |
| `day_of_week` | Integer, 0-6 | Local date; Monday = 0, Sunday = 6. Treat as a category, not an ordered effect. |
| `is_weekend` | Boolean | Local day is Saturday or Sunday. |
| `is_public_holiday` | Boolean | Local date appears in the selected holiday calendar. |
| `holiday_name` | Nullable string | Name from that calendar, absent on ordinary days. |
| `is_long_weekend` | Boolean | Friday–Sunday around a Friday holiday; Saturday–Monday around a Monday holiday. |
| `special_event` | Nullable category | Relevant scheduled event known at confirmation, such as a festival or sports event. |

A normal weekend, a public holiday, and a special event are distinct. Calendars
are supplied by the caller and default to empty. No official holiday calendar is
bundled. A direct holiday-name override affects that order; supply the calendar
in advance to mark the entire long weekend consistently. Labels do not themselves
change traffic or demand.

## Outcomes and lifecycle

These fields describe what happens after confirmation. They are not features
for the original ETA prediction.

| Field | Type | Definition |
| --- | --- | --- |
| `status` | Category | At minimum: active, delivered, or cancelled. Detailed event states can be added when needed. |
| `prep_started_at` | Nullable timestamp | Preparation actually begins. |
| `ready_at` | Nullable timestamp | Food becomes ready for collection. |
| `picked_up_at` | Nullable timestamp | Courier collects this order. |
| `delivered_at` | Nullable timestamp | Customer receives this order. |
| `cancelled_at` | Nullable timestamp | This order is cancelled. |
| `cancelled_by` | Nullable category | Customer, restaurant, or platform; defined only for cancellations. |
| `cancellation_reason` | Nullable category | `changed_mind` (customer), `item_unavailable` (restaurant), or `fulfillment_issue` (platform). |

Courier rejection or reassignment is not automatically order cancellation.
Record assignment changes separately. Events already observed before cancellation
remain recorded; events that never happened remain missing.

- `delivery_duration_minutes` is `delivered_at - confirmed_at`, converted to minutes.
- `late_delivery` is `delivered_at > promised_delivery_at`, for delivered orders.
- Both labels remain missing for active and cancelled orders. Cancellation is
  neither a zero-minute delivery nor an on-time delivery.
- An order cannot be both delivered and cancelled in this simulation. Every
  observed milestone must respect event order; missing timestamps are not zeros.
- Preparation and courier assignment can overlap. Do not double-count overlapping
  waits by adding them together to manufacture total delivery duration.

Evaluate ETA errors only where delivery is observed and disclose that population.
Report cancellations and still-active orders separately, using clearly defined
confirmation cohorts and observation cutoffs. Simulation-only hypothetical
delivery times for cancelled orders, if ever generated, are not observed labels.
A risk model trained only on delivered orders estimates lateness conditional on
delivery, not the probability of cancellation or overall fulfillment failure.

## Multi-order delivery history

Run assignments need `order_id`, `run_id`, `assigned_at`, and an optional removal
timestamp. At most one run can actively own an order at a time. Each run has
one `courier_id`; reassignment ends the old link before a new link takes over.

Run stops identify the order, pickup or drop-off, zone, sequence, and whether the
stop was completed or cancelled. Preserve timestamped stop-plan revisions when
orders are added or removed. A pickup must precede its order's drop-off, and a
cancelled order must not later receive a completed drop-off. Stop plans need not
be calculated from a map.

### Nearby-order batching rule

Additional orders may share a courier only when their pickups and drop-offs are
near the first order's respective stops. Anchor every added order to that first
order; do not allow a chain of individually nearby additions to drift far away.

Use simulated pickup-to-pickup and drop-off-to-drop-off distances for eligibility.
These are different from an order's own `distance_km`: a short delivery can still
be far from the first order. Zone membership alone does not prove proximity.
Sample and retain the proximity values consistently, rather than redraw them
each time the same candidate is checked.

The default cutoff is 1 km for both gaps, with at most two orders per run.
Both are configurable. Orders may join only before the first order is picked up
and while that first order remains active. The capacity counts all orders ever
added to the run; cancellation does not reopen a membership slot. Eligibility
does not guarantee assignment: timing and courier capacity still apply.

Represent batching with a simple extra-travel and pickup/drop-off delay rule.
Nearby orders are not costless additions; traffic and weather affect travel time.
This approximates detours without calculating an actual route. Candidate
proximity and batching decisions belong to assignment-time records, not future
information added to the original confirmation-time feature snapshot.

Final courier identity, batch membership, stop sequence, and realized detour are
diagnostics, not confirmation-time features. Do not retroactively attach future
group members or route decisions to an earlier prediction snapshot.

Shared courier activity and restaurant queues must drive their snapshots. We
will not independently randomize backlog, courier counts, batch flags, and
delivery outcomes in ways that contradict one another.

## Current simulator rules

These are transparent toy assumptions, not calibrated estimates of Toronto.

- **Population:** 5 restaurants, 100 customers, and 3 couriers per zone by default.
  Each restaurant/customer has a fixed zone. Couriers move across zones; they do
  not reset between orders. Zero starting couriers is allowed. Everyone remains
  online and restaurants operate around the clock; no shifts or opening hours.
- **Arrivals:** the gap in minutes is uniform from `20 / orders_per_hour` to
  `100 / orders_per_hour`. Its mean is `60 / orders_per_hour`, so 20/hour gives
  1–5 minute gaps. The rate is an expectation, not an exact hourly count. Arrival
  randomness is separate from operational randomness. Time advances in UTC to
  respect daylight-saving changes. No automatic peak-hour demand multiplier.
- **Identity/baskets:** independently sample a restaurant and customer uniformly.
  Sample 1–5 items unless overridden; subtotal is item count times a sampled
  CAD 8–20 average item price. No tax, fees, or tip. Customer-group add-ons are
  explicit calls with an existing group ID, not automatically sampled arrivals.
- **Distances:** standalone distance is uniform 0.3–2 km within a zone and 1–5 km
  across zones. Gaps between distinct restaurants/customers are uniform 0.1–2 km
  within a zone and 1–5 km across zones; the same location has a zero gap. Cache
  each symmetric pair. These samples are not coordinates or a geometric metric.
- **Preparation:** one simultaneous preparation slot per restaurant, first-in,
  first-out. Duration is `(uniform(8, 16) + 2 * item_count) * prep_time_multiplier`
  minutes. Cancellation releases the slot immediately; its obsolete ready event
  cannot resurrect the order. Preparation and courier assignment may overlap.
- **Dispatch:** consider waiting orders oldest first. Prefer an eligible nearby
  batch; otherwise choose an idle courier from the pickup zone, or any other zone
  if none is local. No reassignment, rejection, optimization, or mid-trip batching.
- **Stops/travel:** collect all pickups in assignment order, then complete drop-offs
  in assignment order. Initial courier approach is sampled at 0.2–1 km in-zone or
  1–3 km cross-zone. Between consecutive pickups/drop-offs use the cached gap;
  from the final pickup to first drop-off use that delivered order's standalone
  distance as a simple trunk-leg approximation. Thus added pickups/drop-offs cost
  extra travel plus one minute of handling per stop, even at a shared location.
  Road time is `distance_km / 0.25 * traffic_index * weather_delay`: 15 km/h baseline.
  A courier waits if the food is not ready. Conditions are sampled at leg departure,
  not taken from that order's future outcomes or retrospectively applied mid-leg.
- **Weather:** one deterministic hourly draw shared by the market: 70% clear,
  25% rain (snow when the base temperature is at/below zero), 5% storm. Toy monthly
  temperatures are `[-3, -2, 3, 9, 15, 21, 24, 23, 19, 12, 6, 0]` Celsius plus
  uniform −3 to +3 degrees. Snow is capped at 0°C; rain/storm floored at 1°C.
  Default precipitation is 0/3/2/10 mm/hour for clear/rain/snow/storm. Their base
  travel multipliers are 1/1.15/1.35/1.5, plus `0.01 * precipitation_mm_per_hour`.
  Temperature is context, not an additional direct delay coefficient. Explicit
  weather controls override these rules; incompatible combinations are rejected.
- **Traffic:** default 1.5 during local hours 7, 8, 16, 17, and 18; otherwise 1.
  Callers can override it. Traffic changes road travel, never kitchen preparation
  or the original promise. Holiday/event labels alone change neither rate nor delay.
- **Cancellation:** a 3% chance to schedule an attempt 1–30 minutes after confirmation.
  It succeeds only if the order is still active and not picked up; the realized
  cancellation share can therefore be below 3%. Actor/reason pairs are sampled
  uniformly at cancellation. Keep earlier milestones and cancel pending stops.
  An already-started courier leg/handling interval finishes before skipping the
  cancelled stop; a courier waiting for cancelled food moves on immediately.
- **Promise/labels:** promise is confirmation plus 45 minutes by default, independent
  of any model. Derive duration and lateness only when delivery completes. A live
  call returns a detached snapshot with all outcome fields missing; advancing the
  market updates its retained order, never that earlier snapshot. A historical
  batch returns order copies as observed at midnight following the last local
  date. There is no forced completion of remaining active orders.

The market contains operational state, including future scheduled events. It
must not be used as a model feature record. Final assignments, batch membership,
and stop histories are diagnostics stored separately from the 37 order columns.
The CLI exports order rows only; pass a market to the Python wrapper to inspect
its run and assignment histories. State persistence/restart and actual API calls
are not implemented. A batch uses O(number of orders) retained history; it is not
an indefinitely running, memory-bounded service.
