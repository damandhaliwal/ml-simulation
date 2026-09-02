# Order schema

Agreed domain design, not implemented code or a database migration. The records
below describe what information we need; they do not require separate services,
classes, or a database at this stage.

## Market boundary

- One synthetic, busy downtown market inspired by Toronto, with many restaurants
  and orders spread across multiple zones. Cross-zone deliveries are supported.
- Zones share a city context but can have different demand, courier supply,
  traffic, weather, and event exposure. They are not isolated simulations.
- Use synthetic identifiers and zone labels, not real customers or restaurant data.
  No coordinate system, map sketch, street network, or shortest-path calculation
  is required for this simulation.
- Store timezone-aware timestamps in UTC. Derive local calendar features using
  `America/Toronto`, including daylight-saving changes.
- The zone labels/count, restaurant count, order volume, and distance distributions
  are intentionally not chosen yet. Geographic scope does not set simulation size.

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

Keep restaurant and customer identities and their zone labels consistent across
records. Sample positive travel distances, with shorter distances more common
within a zone than across zones. Numerical distributions will be specified with
the first generator. These values approximate travel, not a geometric street map.

## Confirmation-time context

Freeze this snapshot before the new order changes queues or courier assignments.
Do not overwrite it as the marketplace evolves.

| Field | Type | Definition |
| --- | --- | --- |
| `restaurant_backlog` | Nonnegative integer | Existing orders awaiting or undergoing preparation at this restaurant; excludes this order and food already ready for pickup. |
| `idle_couriers` | Nonnegative integer | Online, unassigned couriers currently in the pickup zone. |
| `busy_couriers` | Nonnegative integer | Online couriers currently in the pickup zone with an active assignment. Some may be eligible for another order. |
| `orders_waiting_for_courier` | Nonnegative integer | Existing active orders with pickup in this zone and no current courier assignment. |
| `basket_value_cad` | Positive number | Restaurant item subtotal in synthetic Canadian dollars, before tax, tips, and delivery fees. |
| `item_count` | Positive integer | Number of items in this restaurant order. |
| `traffic_index` | Positive number | Observed route-context multiplier relative to baseline road travel time: 1.0 is baseline, 1.5 means 50% longer road travel under those conditions. |
| `weather_type` | Category | Weather observed at confirmation; category definitions remain to be chosen. |
| `temperature_c` | Number | Observed temperature in degrees Celsius. |
| `precipitation_mm_per_hour` | Nonnegative number | Observed precipitation intensity, using water-equivalent millimetres per hour. |

Traffic affects road travel, not the whole delivery duration. The traffic index
must not be calculated from this order's eventual travel time. Weather and
traffic are snapshots, not perfect knowledge of conditions later in the journey.
Their spatial aggregation across a route remains a simulator-design choice.

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
| `is_long_weekend` | Boolean | Derived from the holiday calendar using a rule still to be agreed. |
| `special_event` | Nullable category | Relevant scheduled event known at confirmation, such as a festival or sports event. |

A normal weekend, a public holiday, and a special event are distinct. Calendar
and event policies need explicit definitions before generating these fields.

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
| `cancellation_reason` | Nullable category | Reason recorded at cancellation; the reason taxonomy is not chosen yet. |

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

The distance cutoff is a configurable simulator assumption to choose before
implementing batching. No kilometre limit or percentage of the first trip has
been agreed yet. Eligibility does not guarantee assignment: timing, courier
capacity, and cancellation rules still apply.

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

## Remaining choices, before simulator implementation

1. Zone labels, order timing, and sampled-distance distributions for a tiny input generator.
2. Restaurant and courier populations, capacities, and order-arrival patterns.
3. Traffic/weather dynamics and holiday, long-weekend, and event calendars.
4. Preparation, assignment, cancellation, and batching rules, including proximity
   cutoffs and additional travel/stop delays.
5. The original promise-setting policy, using only information available then.

The next step addresses only item 1. Richer scope remains agreed, but none of
these policies, field generators, or operational mechanisms is implemented yet.
