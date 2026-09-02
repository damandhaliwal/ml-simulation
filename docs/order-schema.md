# Synthetic order data

One row is one customer's purchase from one restaurant. This is a **direct data
generator**, not a running marketplace. Rows carry no queues, events, courier
assignments, or intermediate preparation/pickup timestamps.

There are 31 columns: the 27 order/context columns plus four final outcome
columns. All values and relationships are synthetic.

## Order/context columns

| Columns | Generation / meaning |
| --- | --- |
| `order_id`, `order_group_id` | Caller-supplied order ID, or sequential batch ID; one synthetic group per row. No customer add-on groups are linked. |
| `customer_id`, `restaurant_id`, `service_area_id` | Sample a customer and restaurant; one fictional downtown service area. Default population: 100 customers and 5 restaurants per zone. |
| `pickup_zone_id`, `dropoff_zone_id` | Derived from restaurant/customer IDs; identity-to-zone mapping stays consistent for a fixed zone configuration. |
| `distance_km` | Uniform 0.3–2 km within a zone, 1–5 km across zones. Sampled per order, not calculated from a map or cached across trips. |
| `confirmed_at`, `promised_delivery_at` | UTC timestamps; promise is confirmation plus `promise_minutes`, default 45. |
| `restaurant_backlog` | Sample an integer 0–6, multiply by demand load, and round. |
| `idle_couriers`, `busy_couriers` | Busy = rounded uniform 0.2–0.8 × demand load × local courier count, capped at that count. Idle = local count minus busy. |
| `orders_waiting_for_courier` | Rounded integer draw 0–8 × demand load, minus idle couriers, floored at zero. |
| `item_count`, `basket_value_cad` | Sample 1–5 items unless overridden; subtotal = item count × uniform CAD 8–20. No tax, tip, or fees. |
| `traffic_index` | 1.5 at local hours 7, 8, 16, 17, 18; otherwise 1. Caller override takes precedence. |
| `weather_type`, `temperature_c`, `precipitation_mm_per_hour` | Sample weather and seasonal temperature, or use caller overrides. Details below. |
| `local_hour`, `day_of_week`, `is_weekend` | Derived from Toronto-local confirmation time. Monday = 0; weekends are Saturday/Sunday. |
| `is_public_holiday`, `holiday_name`, `is_long_weekend` | Use the supplied calendar/holiday override. Long weekends are Friday–Sunday for Friday holidays or Saturday–Monday for Monday holidays. |
| `special_event` | Supplied event calendar or direct name override; absent otherwise. |

Demand load is `orders_per_hour / 20`. These workload values are sampled
conditions, not counts recovered from an order history. No fleet or restaurant
state is conserved across rows. Zero local couriers can still lead to a generated
delivery: the formula approximates eventual service, not a fixed-fleet constraint.

Default weather is drawn independently per row: 70% clear, 25% rain/snow, 5% storm.
Toy monthly temperature centres are −3, −2, 3, 9, 15, 21, 24, 23, 19, 12, 6, 0°C,
plus uniform −3 to +3°C. The wet draw is snow when the sampled temperature is
at/below zero, otherwise rain. Snow temperatures are capped at zero; rain/storm
temperatures floored at 1°C. Storm means heavy rain here.

Default precipitation is 0/3/2/10 mm/hour for clear/rain/snow/storm, respectively.
Contradictory weather overrides are rejected. No official Toronto weather or
holiday data is used. Holiday/event labels do not automatically change workload
or traffic; callers control those effects separately.

## Delivery-time formula

These are scalar calculations, not intermediate operational stages:

```python
preparation = (12 + 2 * item_count + 1.5 * backlog) * prep_time_multiplier
supply_delay = 2 * waiting_orders / (idle_couriers + 1)
travel = 4 * (distance_km + detour_km) * traffic_index * weather_multiplier
duration = max(5, preparation + supply_delay + travel + 2 * (1 + extra_orders) + noise)
```

- All duration terms are in minutes. Round the final result to two decimals.
- Noise is a normal draw with mean 0 and standard deviation 3 minutes.
- Weather multiplier = 1/1.15/1.35/1.5 for clear/rain/snow/storm, plus
  `0.01 * precipitation_mm_per_hour`. Temperature has no additional direct effect.
- With probability 0.2, sample additional orders up to `max_orders_per_run - 1`.
  Each additional order adds a sampled pickup gap and drop-off gap, each bounded
  by `batch_max_gap_km` (default 1 km), plus two minutes of stop overhead.
  The two sampled gaps are reused per extra order. This is a hypothetical nearby-
  batching delay, not a route or a count of linked rows in the dataset.
- The batch draw and noise are not returned as features: they represent future,
  unobserved variation in the target.
- Larger baskets/backlogs, fewer idle couriers, heavier traffic, and worse weather
  increase the corresponding duration terms. These coefficients are assumptions,
  not estimated real-world effects.

## Final outcome columns — not inference features

| Column | Meaning |
| --- | --- |
| `status` | A direct cancellation draw (default probability 0.03), otherwise delivered. No active/intermediate state. |
| `delivered_at` | Confirmation plus generated duration; missing for cancellations. |
| `delivery_duration_minutes` | Primary target; missing for cancellations, never zero-filled. |
| `late_delivery` | Duration exceeds original promise; missing for cancellations. |

Outcomes are generated upfront, including deliveries beyond the requested
confirmation-date window. There is no observation cutoff or delayed event engine.
A future live-test harness must withhold these outcomes from predictions.

Removed from the earlier operational schema: `prep_started_at`, `ready_at`,
`picked_up_at`, `cancelled_at`, `cancelled_by`, `cancellation_reason`,
and separate run/assignment/stop histories. Retain cancellations for coverage
reporting; evaluate delivery-time predictions only where delivery is observed.
