import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from random import Random
from zoneinfo import ZoneInfo

ZONES = ("Z1", "Z2", "Z3")
MARKET_TIMEZONE = ZoneInfo("America/Toronto")


def generate_orders(
    start_date: date = date(2026, 1, 1),
    end_date: date = date(2026, 8, 31),
    seed: int = 42,
) -> list[dict[str, str | float]]:
    """Generate inputs between Toronto-local dates, both inclusive."""
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    rng = Random(seed)
    confirmed_at = datetime.combine(start_date, time.min, MARKET_TIMEZONE)
    stop_at = datetime.combine(end_date + timedelta(days=1), time.min, MARKET_TIMEZONE)
    # Advance in UTC so arrival gaps remain valid across daylight-saving changes.
    confirmed_at = confirmed_at.astimezone(timezone.utc)
    stop_at = stop_at.astimezone(timezone.utc)
    orders = []

    while True:
        confirmed_at += timedelta(minutes=rng.randint(1, 5))
        if confirmed_at >= stop_at:
            break
        pickup = rng.choice(ZONES)
        dropoff = rng.choice(ZONES)
        low, high = (0.3, 2.0) if pickup == dropoff else (1.0, 5.0)

        orders.append(
            {
                "order_id": f"O{len(orders) + 1:06d}",
                "confirmed_at": confirmed_at.isoformat(),
                "pickup_zone_id": pickup,
                "dropoff_zone_id": dropoff,
                "distance_km": round(rng.uniform(low, high), 2),
            }
        )

    return orders


if __name__ == "__main__":
    orders = generate_orders()
    output = Path("data/orders_2026_jan_aug.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        json.dump(orders, file, indent=2)
        file.write("\n")
    print(f"Generated {len(orders):,} orders: {output}")
