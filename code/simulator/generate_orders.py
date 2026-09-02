import argparse
import json
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from pathlib import Path
from random import Random
from zoneinfo import ZoneInfo

ZONES = ("Z1", "Z2", "Z3")
MARKET_TIMEZONE = ZoneInfo("America/Toronto")
MONTHLY_TEMPERATURE = (-3, -2, 3, 9, 15, 21, 24, 23, 19, 12, 6, 0)
WEATHER = {"clear": (0, 1.0), "rain": (3, 1.15), "snow": (2, 1.35), "storm": (10, 1.5)}


def generate_order(
    confirmed_at: datetime, *, order_id: str = "O000001", seed: int = 42,
    orders_per_hour: float = 20, couriers_per_zone: int = 3,
    restaurants_per_zone: int = 5, zones=ZONES,
    traffic_index: float | None = None, weather_type: str | None = None,
    temperature_c: float | None = None, precipitation_mm_per_hour: float | None = None,
    holidays: dict[date, str] | None = None, holiday_name: str | None = None,
    special_events: dict[date, str] | None = None, special_event: str | None = None,
    item_count: int | None = None, prep_time_multiplier: float = 1,
    promise_minutes: float = 45, cancellation_probability: float = 0.03,
    batch_probability: float = 0.2, max_orders_per_run: int = 2,
    batch_max_gap_km: float = 1,
) -> dict:
    """Sample one complete synthetic row. No state, queues, or event processing."""
    if not isinstance(confirmed_at, datetime) or confirmed_at.utcoffset() is None:
        raise ValueError("confirmed_at must be a timezone-aware datetime")
    zones = tuple(zones)
    if (not zones or any(not isinstance(z, str) or not z for z in zones)
            or len(set(zones)) != len(zones)):
        raise ValueError("zones must be distinct nonempty strings")
    if not isinstance(order_id, str) or not order_id:
        raise ValueError("order_id must be a nonempty string")
    positive = (orders_per_hour, prep_time_multiplier, promise_minutes)
    nonnegative = (batch_max_gap_km, cancellation_probability, batch_probability)
    if any(not isinstance(x, (int, float)) or isinstance(x, bool)
           or not isfinite(x) or x <= 0 for x in positive):
        raise ValueError("arrival rate, preparation multiplier, and promise must be positive and finite")
    if any(not isinstance(x, (int, float)) or isinstance(x, bool)
           or not isfinite(x) or x < 0 for x in nonnegative):
        raise ValueError("batch gap and probabilities must be nonnegative and finite")
    if cancellation_probability > 1 or batch_probability > 1:
        raise ValueError("probabilities must be between 0 and 1")
    for value, minimum in ((couriers_per_zone, 0), (restaurants_per_zone, 1),
                           (max_orders_per_run, 1), (item_count if item_count is not None else 1, 1)):
        if type(value) is not int or value < minimum:
            raise ValueError("population, item, and batch counts must be valid integers")
    for calendar in (holidays, special_events):
        if calendar is not None and any(type(d) is not date or not isinstance(name, str) or not name
                                        for d, name in calendar.items()):
            raise ValueError("calendars must map dates to nonempty names")
    for name in (holiday_name, special_event):
        if name is not None and (not isinstance(name, str) or not name):
            raise ValueError("holiday/event names must be nonempty strings")

    confirmed_at = confirmed_at.astimezone(timezone.utc)
    local = confirmed_at.astimezone(MARKET_TIMEZONE)
    rng = Random(f"{seed}:{order_id}")
    restaurant = rng.randrange(restaurants_per_zone * len(zones))
    customer = rng.randrange(100 * len(zones))
    pickup, dropoff = zones[restaurant % len(zones)], zones[customer % len(zones)]
    distance = round(rng.uniform(0.3, 2) if pickup == dropoff else rng.uniform(1, 5), 2)
    items = rng.randint(1, 5) if item_count is None else item_count

    temperature = MONTHLY_TEMPERATURE[local.month - 1] + rng.uniform(-3, 3)
    weather = rng.choices(
        ("clear", "snow" if temperature <= 0 else "rain", "storm"), (0.7, 0.25, 0.05)
    )[0]
    weather = weather if weather_type is None else weather_type
    if not isinstance(weather, str) or weather not in WEATHER:
        raise ValueError(f"weather_type must be one of {tuple(WEATHER)}")
    if weather == "snow":
        temperature = min(temperature, 0)
    elif weather in ("rain", "storm"):
        temperature = max(temperature, 1)
    temperature = round(temperature, 1) if temperature_c is None else temperature_c
    rain = WEATHER[weather][0] if precipitation_mm_per_hour is None else precipitation_mm_per_hour
    traffic = 1.5 if local.hour in (7, 8, 16, 17, 18) else 1
    traffic = traffic if traffic_index is None else traffic_index
    if any(not isinstance(x, (int, float)) or isinstance(x, bool)
           or not isfinite(x) for x in (temperature, rain, traffic)):
        raise ValueError("weather and traffic values must be finite numbers")
    if traffic <= 0 or rain < 0 or (weather == "clear") != (rain == 0):
        raise ValueError("traffic must be positive; precipitation must match the weather")
    if ((weather == "snow" and temperature > 0)
            or (weather in ("rain", "storm") and temperature <= 0)):
        raise ValueError("snow needs temperature <= 0; rain/storm need temperature > 0")

    calendar = dict(holidays or {})
    if holiday_name is not None:
        calendar[local.date()] = holiday_name
    monday = local.date() - timedelta(days=local.weekday())
    long_weekend = (local.weekday() >= 4 and monday + timedelta(days=4) in calendar)
    long_weekend |= (local.weekday() >= 5 and monday + timedelta(days=7) in calendar)
    long_weekend |= local.weekday() == 0 and local.date() in calendar

    # Related sampled conditions, not counts reconstructed from a running market.
    load = orders_per_hour / 20
    backlog = round(rng.randint(0, 6) * load)
    busy = min(couriers_per_zone, round(rng.uniform(0.2, 0.8) * load * couriers_per_zone))
    idle = couriers_per_zone - busy
    waiting = max(0, round(rng.randint(0, 8) * load) - idle)

    # A nearby-batch delay is unobserved target variation, not a prediction feature.
    extra_orders = rng.randint(1, max_orders_per_run - 1) if max_orders_per_run > 1 else 0
    extra_orders *= rng.random() < batch_probability
    detour_km = extra_orders * (
        rng.uniform(0, batch_max_gap_km) + rng.uniform(0, batch_max_gap_km)
    )
    preparation = (12 + 2 * items + 1.5 * backlog) * prep_time_multiplier
    supply_delay = 2 * waiting / (idle + 1)
    travel = 4 * (distance + detour_km) * traffic * (WEATHER[weather][1] + 0.01 * rain)
    duration = round(max(5, preparation + supply_delay + travel
                         + 2 * (1 + extra_orders) + rng.gauss(0, 3)), 2)
    cancelled = rng.random() < cancellation_probability

    return {
        "order_id": order_id, "order_group_id": f"G-{order_id}",
        "customer_id": f"C{customer:04d}", "restaurant_id": f"R{restaurant:03d}",
        "service_area_id": "DOWNTOWN_SIM", "pickup_zone_id": pickup, "dropoff_zone_id": dropoff,
        "distance_km": distance, "confirmed_at": confirmed_at.isoformat(),
        "promised_delivery_at": (confirmed_at + timedelta(minutes=promise_minutes)).isoformat(),
        "restaurant_backlog": backlog, "idle_couriers": idle, "busy_couriers": busy,
        "orders_waiting_for_courier": waiting, "item_count": items,
        "basket_value_cad": round(items * rng.uniform(8, 20), 2),
        "traffic_index": traffic, "weather_type": weather, "temperature_c": temperature,
        "precipitation_mm_per_hour": rain, "local_hour": local.hour, "day_of_week": local.weekday(),
        "is_weekend": local.weekday() >= 5, "is_public_holiday": local.date() in calendar,
        "holiday_name": calendar.get(local.date()), "is_long_weekend": long_weekend,
        "special_event": special_event or (special_events or {}).get(local.date()),
        "status": "cancelled" if cancelled else "delivered",
        "delivered_at": None if cancelled else (confirmed_at + timedelta(minutes=duration)).isoformat(),
        "delivery_duration_minutes": None if cancelled else duration,
        "late_delivery": None if cancelled else duration > promise_minutes,
    }


def generate_orders(
    start_date: date, end_date: date, seed: int = 42, *,
    orders_per_hour: float = 20, **order_options,
) -> list[dict]:
    """Generate complete rows for an inclusive range of Toronto-local dates."""
    if type(start_date) is not date or type(end_date) is not date or end_date < start_date:
        raise ValueError("supply dates with end_date on or after start_date")
    if (not isinstance(orders_per_hour, (int, float)) or isinstance(orders_per_hour, bool)
            or not isfinite(orders_per_hour) or orders_per_hour <= 0):
        raise ValueError("orders_per_hour must be positive and finite")
    if 1200 / orders_per_hour < 1e-6:
        raise ValueError("arrival gaps must be at least one microsecond")
    at = datetime.combine(start_date, time.min, MARKET_TIMEZONE).astimezone(timezone.utc)
    stop = datetime.combine(end_date + timedelta(days=1), time.min, MARKET_TIMEZONE).astimezone(timezone.utc)
    # Validate even a window with no arrivals; the discarded row changes no state.
    generate_order(at, seed=seed, orders_per_hour=orders_per_hour, **order_options)
    rng, orders = Random(seed), []
    while True:
        if 20 / orders_per_hour >= (stop - at).total_seconds() / 60:
            break
        gap = rng.uniform(20 / orders_per_hour, 100 / orders_per_hour)
        if gap >= (stop - at).total_seconds() / 60:
            break
        at += timedelta(minutes=gap)
        order_id = f"O{len(orders) + 1:06d}"
        orders.append(generate_order(at, seed=seed, order_id=order_id,
                                     orders_per_hour=orders_per_hour, **order_options))
    return orders


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic delivery-time data.")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--orders-per-hour", type=float, default=20)
    args = parser.parse_args()
    orders = generate_orders(args.start, args.end, args.seed, orders_per_hour=args.orders_per_hour)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as file:
        json.dump(orders, file, indent=2)
        file.write("\n")
    print(f"Generated {len(orders):,} orders: {args.output}")
