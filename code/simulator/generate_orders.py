import argparse
import json
from datetime import date, datetime, time, timedelta, timezone
from heapq import heappop, heappush
from math import isfinite
from pathlib import Path
from random import Random
from zoneinfo import ZoneInfo

ZONES = ("Z1", "Z2", "Z3")
MARKET_TIMEZONE = ZoneInfo("America/Toronto")
WEATHER = {
    "clear": (0.0, 1.0), "rain": (3.0, 1.15),
    "snow": (2.0, 1.35), "storm": (10.0, 1.5),
}
OUTCOME_FIELDS = (
    "prep_started_at", "ready_at", "picked_up_at", "delivered_at",
    "cancelled_at", "cancelled_by", "cancellation_reason",
    "delivery_duration_minutes", "late_delivery",
)


def _number(name, value, minimum=0, *, positive=False, integer=False):
    valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
             and isfinite(value) and value >= minimum)
    if not valid or (positive and value == 0) or (integer and not isinstance(value, int)):
        raise ValueError(f"{name} must be a finite {'integer' if integer else 'number'} "
                         f"{'>' if positive else '>='} {minimum}")


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware datetimes")
    return value.astimezone(timezone.utc)


def create_market(
    seed: int = 42, *, zones=ZONES, restaurants_per_zone: int = 5,
    customers_per_zone: int = 100, couriers_per_zone: int = 3,
    max_orders_per_run: int = 2, batch_max_gap_km: float = 1.0,
    cancellation_probability: float = 0.03, promise_minutes: float = 45,
    holidays: dict[date, str] | None = None,
    special_events: dict[date, str] | None = None,
) -> dict:
    """Create one persistent, seeded market; policies are synthetic assumptions."""
    zones = tuple(zones)
    if (not zones or any(not isinstance(z, str) or not z for z in zones)
            or len(set(zones)) != len(zones)):
        raise ValueError("zones must contain distinct, nonempty strings")
    for name, value in (("restaurants_per_zone", restaurants_per_zone),
                        ("customers_per_zone", customers_per_zone),
                        ("max_orders_per_run", max_orders_per_run)):
        _number(name, value, positive=True, integer=True)
    _number("couriers_per_zone", couriers_per_zone, integer=True)
    _number("batch_max_gap_km", batch_max_gap_km)
    _number("promise_minutes", promise_minutes, positive=True)
    _number("cancellation_probability", cancellation_probability)
    if cancellation_probability > 1:
        raise ValueError("cancellation_probability must be <= 1")
    for calendar in (holidays, special_events):
        if calendar is not None and any(
            type(day) is not date or not isinstance(label, str) or not label
            for day, label in calendar.items()
        ):
            raise ValueError("calendars must map dates to nonempty names")
    restaurants = {
        f"R{z:02d}{i:03d}": {"zone_id": zone, "queue": [], "cooking": None}
        for z, zone in enumerate(zones) for i in range(restaurants_per_zone)
    }
    customers = {f"C{z:02d}{i:03d}": zone for z, zone in enumerate(zones)
                 for i in range(customers_per_zone)}
    couriers = {f"K{z:02d}{i:03d}": {"zone_id": zone, "run_id": None}
                for z, zone in enumerate(zones) for i in range(couriers_per_zone)}
    return {
        "seed": seed, "rng": Random(seed), "arrival_rng": Random(f"{seed}:arrivals"),
        "now": None, "events": [], "event_number": 0, "restaurants": restaurants,
        "customers": customers, "couriers": couriers, "orders": {}, "active": {},
        "groups": {}, "prep_minutes": {}, "waiting": {}, "runs": {},
        "assignments": [], "assigned": {}, "gaps": {}, "conditions": {},
        "max_orders_per_run": max_orders_per_run, "batch_max_gap_km": batch_max_gap_km,
        "cancellation_probability": cancellation_probability, "promise_minutes": promise_minutes,
        "holidays": dict(holidays or {}), "special_events": dict(special_events or {}),
    }


def _conditions(market, at, overrides):
    local = at.astimezone(MARKET_TIMEZONE)
    # Shared hourly context, independent of how many orders or events we process.
    hour = at.replace(minute=0, second=0, microsecond=0)
    rng = Random(f"{market['seed']}:weather:{hour}")
    temperature = (-3, -2, 3, 9, 15, 21, 24, 23, 19, 12, 6, 0)[local.month - 1]
    temperature += rng.uniform(-3, 3)
    wet = "snow" if temperature <= 0 else "rain"
    weather = rng.choices(("clear", wet, "storm"), (0.7, 0.25, 0.05))[0]
    if overrides.get("weather_type") is not None:
        weather = overrides["weather_type"]
    if not isinstance(weather, str) or weather not in WEATHER:
        raise ValueError(f"weather_type must be one of {tuple(WEATHER)}")
    if weather == "snow":
        temperature = min(temperature, 0)
    elif weather in ("rain", "storm"):
        temperature = max(temperature, 1)
    result = {
        "traffic_index": 1.5 if local.hour in (7, 8, 16, 17, 18) else 1.0,
        "weather_type": weather, "temperature_c": round(temperature, 1),
        "precipitation_mm_per_hour": WEATHER[weather][0],
    }
    result.update({key: value for key, value in overrides.items() if value is not None})
    _number("traffic_index", result["traffic_index"], positive=True)
    _number("temperature_c", result["temperature_c"], minimum=-100)
    _number("precipitation_mm_per_hour", result["precipitation_mm_per_hour"])
    if (weather == "clear") != (result["precipitation_mm_per_hour"] == 0):
        raise ValueError("clear weather requires zero precipitation; wet weather requires > 0")
    if weather == "snow" and result["temperature_c"] > 0:
        raise ValueError("snow requires temperature_c <= 0 in this toy simulator")
    if weather in ("rain", "storm") and result["temperature_c"] <= 0:
        raise ValueError("rain/storm require temperature_c > 0 in this toy simulator")
    return result


def _calendar(market, at, holiday_name, special_event):
    local = at.astimezone(MARKET_TIMEZONE)
    day = local.date()
    holidays = market["holidays"] | ({day: holiday_name} if holiday_name else {})
    friday = day + timedelta(days=4 - day.weekday())
    monday = day - timedelta(days=day.weekday())
    long_weekend = (day.weekday() in (4, 5, 6) and friday in holidays)
    long_weekend |= (day.weekday() in (5, 6) and monday + timedelta(days=7) in holidays)
    long_weekend |= day.weekday() == 0 and day in holidays
    return {
        "local_hour": local.hour, "day_of_week": local.weekday(),
        "is_weekend": local.weekday() >= 5, "is_public_holiday": day in holidays,
        "holiday_name": holidays.get(day), "is_long_weekend": long_weekend,
        "special_event": special_event or market["special_events"].get(day),
    }


def generate_order(
    confirmed_at: datetime, market: dict, *, traffic_index: float | None = None,
    weather_type: str | None = None, temperature_c: float | None = None,
    precipitation_mm_per_hour: float | None = None, holiday_name: str | None = None,
    special_event: str | None = None, prep_time_multiplier: float = 1.0,
    item_count: int | None = None, order_group_id: str | None = None,
) -> dict:
    """Return a confirmation snapshot; the market retains its evolving order separately."""
    confirmed_at = _utc(confirmed_at)
    if weather_type is None and (temperature_c is not None or precipitation_mm_per_hour is not None):
        raise ValueError("provide weather_type when overriding temperature or precipitation")
    overrides = dict(traffic_index=traffic_index, weather_type=weather_type,
                     temperature_c=temperature_c, precipitation_mm_per_hour=precipitation_mm_per_hour)
    context = _conditions(market, confirmed_at, overrides)
    _number("prep_time_multiplier", prep_time_multiplier, positive=True)
    if item_count is not None:
        _number("item_count", item_count, positive=True, integer=True)
    for label in (holiday_name, special_event):
        if label is not None and (not isinstance(label, str) or not label):
            raise ValueError("holiday_name and special_event must be nonempty strings or None")
    group = market["groups"].get(order_group_id)
    if order_group_id is not None and group is None:
        raise ValueError("order_group_id must identify an existing group in this market")
    restaurants = [r for r in market["restaurants"]
                   if group is None or r not in group["restaurants"]]
    if not restaurants:
        raise ValueError("the group already has an order from every restaurant")
    advance_market(market, confirmed_at)
    market["conditions"] = overrides
    rng = market["rng"]
    restaurant_id = rng.choice(restaurants)
    customer_id = group["customer_id"] if group else rng.choice(tuple(market["customers"]))
    pickup = market["restaurants"][restaurant_id]["zone_id"]
    dropoff = market["customers"][customer_id]
    count = item_count if item_count is not None else rng.randint(1, 5)
    order_id = f"O{len(market['orders']) + 1:06d}"
    group_id = order_group_id or f"G{len(market['groups']) + 1:06d}"
    couriers = [c for c in market["couriers"].values() if c["zone_id"] == pickup]
    low, high = (0.3, 2.0) if pickup == dropoff else (1.0, 5.0)
    order = {
        "order_id": order_id, "order_group_id": group_id, "customer_id": customer_id,
        "restaurant_id": restaurant_id, "service_area_id": "DOWNTOWN_SIM",
        "pickup_zone_id": pickup, "dropoff_zone_id": dropoff,
        "distance_km": round(rng.uniform(low, high), 2), "confirmed_at": confirmed_at.isoformat(),
        "promised_delivery_at": (confirmed_at + timedelta(minutes=market["promise_minutes"])).isoformat(),
        "restaurant_backlog": sum(o["restaurant_id"] == restaurant_id and o["ready_at"] is None
                                  for o in market["active"].values()),
        "idle_couriers": sum(c["run_id"] is None for c in couriers),
        "busy_couriers": sum(c["run_id"] is not None for c in couriers),
        "orders_waiting_for_courier": sum(market["orders"][oid]["pickup_zone_id"] == pickup
                                          for oid in market["waiting"]),
        "basket_value_cad": round(count * rng.uniform(8, 20), 2), "item_count": count,
        **context, **_calendar(market, confirmed_at, holiday_name, special_event),
        "status": "active", **dict.fromkeys(OUTCOME_FIELDS),
    }
    snapshot = order.copy()
    market["orders"][order_id] = market["active"][order_id] = order
    market["waiting"][order_id] = None
    market["groups"].setdefault(
        group_id, {"customer_id": customer_id, "restaurants": set()}
    )["restaurants"].add(restaurant_id)
    market["prep_minutes"][order_id] = (rng.uniform(8, 16) + 2 * count) * prep_time_multiplier
    market["restaurants"][restaurant_id]["queue"].append(order_id)
    if rng.random() < market["cancellation_probability"]:
        _schedule(market, confirmed_at + timedelta(minutes=rng.uniform(1, 30)), "cancel", order_id)
    _start_prep(market, restaurant_id)
    _dispatch(market)
    return snapshot


def _schedule(market, at, kind, target):
    market["event_number"] += 1
    heappush(market["events"], (at, market["event_number"], kind, target))


def _start_prep(market, restaurant_id):
    restaurant = market["restaurants"][restaurant_id]
    while restaurant["cooking"] is None and restaurant["queue"]:
        order_id = restaurant["queue"].pop(0)
        order = market["orders"][order_id]
        if order["status"] == "cancelled":
            continue
        restaurant["cooking"] = order_id
        order["prep_started_at"] = market["now"].isoformat()
        _schedule(market, market["now"] + timedelta(minutes=market["prep_minutes"][order_id]),
                  "ready", order_id)


def _gap(market, first, other, kind):
    field = "restaurant_id" if kind == "pickup" else "customer_id"
    if first[field] == other[field]:
        return 0.0
    key = (kind, *sorted((first[field], other[field])))
    if key not in market["gaps"]:
        zone = "pickup_zone_id" if kind == "pickup" else "dropoff_zone_id"
        low, high = (0.1, 2.0) if first[zone] == other[zone] else (1.0, 5.0)
        market["gaps"][key] = round(market["rng"].uniform(low, high), 2)
    return market["gaps"][key]


def _record_plan(market, run):
    run["plan_revisions"].append({
        "revised_at": market["now"].isoformat(),
        "stops": [{"order_id": s["order_id"], "kind": s["kind"], "sequence": i,
                   "status": s["status"]} for i, s in enumerate(run["stops"], 1)],
    })


def _dispatch(market):
    for order_id in list(market["waiting"]):
        order = market["orders"][order_id]
        run = None
        gaps = (0.0, 0.0)
        for courier in market["couriers"].values():
            if courier["run_id"] is None:
                continue
            candidate = market["runs"][courier["run_id"]]
            anchor = market["orders"][candidate["order_ids"][0]]
            if (anchor["status"] != "active" or anchor["picked_up_at"] is not None
                    or len(candidate["order_ids"]) >= market["max_orders_per_run"]):
                continue
            candidate_gaps = tuple(
                _gap(market, anchor, order, kind) for kind in ("pickup", "dropoff")
            )
            if max(candidate_gaps) <= market["batch_max_gap_km"]:
                run, gaps = candidate, candidate_gaps
                break
        if run is None:
            idle = [(key, c) for key, c in market["couriers"].items() if c["run_id"] is None]
            if not idle:
                continue
            local = [pair for pair in idle if pair[1]["zone_id"] == order["pickup_zone_id"]]
            courier_id, courier = market["rng"].choice(local or idle)
            run_id = f"RUN{len(market['runs']) + 1:06d}"
            run = {"run_id": run_id, "courier_id": courier_id, "order_ids": [],
                   "stops": [], "plan_revisions": [], "phase": None,
                   "current_stop": None, "last_stop": None, "status": "active"}
            market["runs"][run_id] = run
            courier["run_id"] = run_id
        run["order_ids"].append(order_id)
        for kind, zone in (("pickup", "pickup_zone_id"), ("dropoff", "dropoff_zone_id")):
            stop = {"order_id": order_id, "kind": kind, "zone_id": order[zone],
                    "status": "pending", "completed_at": None, "cancelled_at": None}
            index = next((i for i, s in enumerate(run["stops"])
                          if s["kind"] == "dropoff"), len(run["stops"]))
            run["stops"].insert(index if kind == "pickup" else len(run["stops"]), stop)
        assignment = {"order_id": order_id, "run_id": run["run_id"],
                      "assigned_at": market["now"].isoformat(), "removed_at": None,
                      "pickup_gap_km": gaps[0], "dropoff_gap_km": gaps[1]}
        market["assignments"].append(assignment)
        market["assigned"][order_id] = assignment
        del market["waiting"][order_id]
        _record_plan(market, run)
        if run["phase"] is None:
            _travel_next(market, run)


def _travel_next(market, run):
    stop = next((s for s in run["stops"] if s["status"] == "pending"), None)
    courier = market["couriers"][run["courier_id"]]
    if stop is None:
        run["status"] = "finished"
        courier["run_id"] = None
        return
    order = market["orders"][stop["order_id"]]
    previous = run["last_stop"]
    if previous is None:
        low, high = (0.2, 1.0) if courier["zone_id"] == stop["zone_id"] else (1.0, 3.0)
        distance = market["rng"].uniform(low, high)
    elif previous["kind"] == stop["kind"]:
        distance = _gap(market, market["orders"][previous["order_id"]], order, stop["kind"])
    else:
        distance = order["distance_km"]
    context = _conditions(market, market["now"], market["conditions"])
    weather_delay = WEATHER[context["weather_type"]][1] + 0.01 * context["precipitation_mm_per_hour"]
    minutes = distance / 0.25 * context["traffic_index"] * weather_delay
    run["current_stop"], run["phase"] = stop, "travel"
    _schedule(market, market["now"] + timedelta(minutes=minutes), "arrive", run["run_id"])


def _service_stop(market, run):
    run["phase"] = "service"
    _schedule(market, market["now"] + timedelta(minutes=1), "stop", run["run_id"])


def _cancel(market, order):
    if order["status"] != "active" or order["picked_up_at"] is not None:
        return
    order["status"], order["cancelled_at"] = "cancelled", market["now"].isoformat()
    order["cancelled_by"], order["cancellation_reason"] = market["rng"].choice((
        ("customer", "changed_mind"), ("restaurant", "item_unavailable"),
        ("platform", "fulfillment_issue"),
    ))
    order_id = order["order_id"]
    del market["active"][order_id]
    market["waiting"].pop(order_id, None)
    restaurant = market["restaurants"][order["restaurant_id"]]
    if restaurant["cooking"] == order_id:
        restaurant["cooking"] = None
        _start_prep(market, order["restaurant_id"])
    assignment = market["assigned"].pop(order_id, None)
    if assignment:
        assignment["removed_at"] = order["cancelled_at"]
        run = market["runs"][assignment["run_id"]]
        for stop in run["stops"]:
            if stop["order_id"] == order_id and stop["status"] == "pending":
                stop["status"], stop["cancelled_at"] = "cancelled", order["cancelled_at"]
        _record_plan(market, run)
        if run["phase"] == "waiting" and run["current_stop"]["order_id"] == order_id:
            run["last_stop"], run["phase"] = run["current_stop"], None
            _travel_next(market, run)


def advance_market(market: dict, until: datetime) -> None:
    """Reveal events through an inclusive observation cutoff; never move time backward."""
    until = _utc(until)
    if market["now"] is not None and until < market["now"]:
        raise ValueError("cannot move market time backward")
    while market["events"] and market["events"][0][0] <= until:
        at, _, kind, target = heappop(market["events"])
        market["now"] = at
        if kind == "cancel":
            _cancel(market, market["orders"][target])
        elif kind == "ready":
            order = market["orders"][target]
            restaurant = market["restaurants"][order["restaurant_id"]]
            if restaurant["cooking"] != target:
                continue  # A cancelled preparation's old event is no longer valid.
            order["ready_at"] = at.isoformat()
            restaurant["cooking"] = None
            _start_prep(market, order["restaurant_id"])
            assignment = market["assigned"].get(target)
            if assignment:
                run = market["runs"][assignment["run_id"]]
                if run["phase"] == "waiting" and run["current_stop"]["order_id"] == target:
                    _service_stop(market, run)
        else:
            run = market["runs"][target]
            stop = run["current_stop"]
            order = market["orders"][stop["order_id"]]
            if kind == "arrive":
                market["couriers"][run["courier_id"]]["zone_id"] = stop["zone_id"]
            if stop["status"] == "cancelled":
                run["last_stop"], run["phase"] = stop, None
                _travel_next(market, run)
            elif kind == "arrive":
                if stop["kind"] == "pickup" and order["ready_at"] is None:
                    run["phase"] = "waiting"
                else:
                    _service_stop(market, run)
            else:
                stop["status"], stop["completed_at"] = "completed", at.isoformat()
                if stop["kind"] == "pickup":
                    order["picked_up_at"] = at.isoformat()
                else:
                    order["status"], order["delivered_at"] = "delivered", at.isoformat()
                    order["delivery_duration_minutes"] = (
                        at - datetime.fromisoformat(order["confirmed_at"])
                    ).total_seconds() / 60
                    order["late_delivery"] = at > datetime.fromisoformat(order["promised_delivery_at"])
                    market["assigned"].pop(order["order_id"])["removed_at"] = at.isoformat()
                    del market["active"][order["order_id"]]
                run["last_stop"], run["phase"] = stop, None
                _travel_next(market, run)
        _dispatch(market)
    market["now"] = until


def generate_orders(
    start_date: date, end_date: date, seed: int = 42, *, market: dict | None = None,
    orders_per_hour: float = 20, **order_options,
) -> list[dict]:
    """Generate a local-date cohort and return outcomes observed at the end boundary.

    order_options are keyword arguments to generate_order. Supply a market to
    configure populations/policies or continue its existing state.
    """
    if type(start_date) is not date or type(end_date) is not date:
        raise ValueError("start_date and end_date must be dates, not timestamps")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    _number("orders_per_hour", orders_per_hour, positive=True)
    if timedelta(minutes=20 / orders_per_hour) <= timedelta(0):
        raise ValueError("orders_per_hour exceeds timestamp resolution")
    market = create_market(seed) if market is None else market
    start = datetime.combine(start_date, time.min, MARKET_TIMEZONE).astimezone(timezone.utc)
    stop = datetime.combine(end_date + timedelta(days=1), time.min, MARKET_TIMEZONE).astimezone(timezone.utc)
    advance_market(market, start)
    order_ids = []
    at = start
    while True:
        gap = market["arrival_rng"].uniform(20 / orders_per_hour, 100 / orders_per_hour)
        at += timedelta(minutes=gap)
        if at >= stop:
            break
        order_ids.append(generate_order(at, market, **order_options)["order_id"])
    advance_market(market, stop)
    return [market["orders"][key].copy() for key in order_ids]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate simulated orders; dates are Toronto-local.")
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
