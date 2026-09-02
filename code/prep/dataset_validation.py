import argparse
import json
from datetime import date, datetime
from math import isfinite
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

MARKET_TIMEZONE = ZoneInfo("America/Toronto")
DEFAULT_TRAIN_RANGE = (date(2026, 1, 1), date(2026, 6, 30))
DEFAULT_VAL_RANGE = (date(2026, 7, 1), date(2026, 7, 31))
DEFAULT_TEST_RANGE = (date(2026, 8, 1), date(2026, 8, 31))


def load_orders(path: Path | str) -> list[dict]:
    """Load and return order records from a JSON file."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Order file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list) or not data:
        raise ValueError("Orders file must contain a nonempty list of order records")
    return data


def separate_cancellations(orders: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separate delivered orders from cancelled orders with integrity checks."""
    delivered: list[dict] = []
    cancelled: list[dict] = []

    for order in orders:
        status = order.get("status")
        duration = order.get("delivery_duration_minutes")

        if status == "delivered":
            if duration is None or not isfinite(duration) or duration <= 0:
                raise ValueError(
                    f"Delivered order {order.get('order_id')} has invalid duration: {duration}"
                )
            delivered.append(order)
        elif status == "cancelled":
            if duration is not None:
                raise ValueError(
                    f"Cancelled order {order.get('order_id')} must not have a delivery duration"
                )
            cancelled.append(order)
        else:
            raise ValueError(f"Unknown order status: {status}")

    return delivered, cancelled


def split_delivered_orders(
    orders: list[dict],
    train_range: tuple[date, date] = DEFAULT_TRAIN_RANGE,
    val_range: tuple[date, date] = DEFAULT_VAL_RANGE,
    test_range: tuple[date, date] = DEFAULT_TEST_RANGE,
) -> dict[str, list[dict]]:
    """Partition delivered orders into chronological train, val, and test splits."""
    for r in (train_range, val_range, test_range):
        if r[0] > r[1]:
            raise ValueError(f"Start date {r[0]} cannot be after end date {r[1]}")
    if train_range[1] >= val_range[0] or val_range[1] >= test_range[0]:
        raise ValueError("Date ranges must be strictly chronological and non-overlapping")

    splits: dict[str, list[dict]] = {"train": [], "val": [], "test": []}

    for order in orders:
        confirmed_at = datetime.fromisoformat(order["confirmed_at"]).astimezone(MARKET_TIMEZONE)
        local_date = confirmed_at.date()

        if train_range[0] <= local_date <= train_range[1]:
            splits["train"].append(order)
        elif val_range[0] <= local_date <= val_range[1]:
            splits["val"].append(order)
        elif test_range[0] <= local_date <= test_range[1]:
            splits["test"].append(order)
        else:
            raise ValueError(
                f"Order {order.get('order_id')} confirmed at {confirmed_at} falls outside all split ranges"
            )

    return splits


def validate_splits(splits: dict[str, list[dict]]) -> dict[str, dict]:
    """Verify partition integrity, absence of leakage, and compute summary stats."""
    expected_splits = ("train", "val", "test")
    if set(splits.keys()) != set(expected_splits):
        raise ValueError(f"Splits dictionary must have exactly keys: {expected_splits}")

    seen_ids: set[str] = set()
    summary: dict[str, dict] = {}
    last_confirmed_by_split: dict[str, datetime] = {}
    first_confirmed_by_split: dict[str, datetime] = {}

    for name in expected_splits:
        rows = splits[name]
        if not rows:
            raise ValueError(f"Split '{name}' is empty")

        durations: list[float] = []
        for row in rows:
            order_id = row["order_id"]
            if order_id in seen_ids:
                raise ValueError(f"Duplicate order_id across splits: {order_id}")
            seen_ids.add(order_id)

            confirmed_at = datetime.fromisoformat(row["confirmed_at"]).astimezone(MARKET_TIMEZONE)
            if name not in first_confirmed_by_split or confirmed_at < first_confirmed_by_split[name]:
                first_confirmed_by_split[name] = confirmed_at
            if name not in last_confirmed_by_split or confirmed_at > last_confirmed_by_split[name]:
                last_confirmed_by_split[name] = confirmed_at

            durations.append(row["delivery_duration_minutes"])

        summary[name] = {
            "count": len(rows),
            "first_confirmed": first_confirmed_by_split[name].isoformat(),
            "last_confirmed": last_confirmed_by_split[name].isoformat(),
            "mean_duration": round(mean(durations), 2),
            "median_duration": round(median(durations), 2),
            "min_duration": round(min(durations), 2),
            "max_duration": round(max(durations), 2),
        }

    # Chronological leakage checks: train ends before val starts; val ends before test starts
    if last_confirmed_by_split["train"] >= first_confirmed_by_split["val"]:
        raise ValueError("Chronological leakage: train orders overlap with or postdate val orders")
    if last_confirmed_by_split["val"] >= first_confirmed_by_split["test"]:
        raise ValueError("Chronological leakage: val orders overlap with or postdate test orders")

    return summary


def prepare_dataset(
    path: Path | str,
    train_range: tuple[date, date] = DEFAULT_TRAIN_RANGE,
    val_range: tuple[date, date] = DEFAULT_VAL_RANGE,
    test_range: tuple[date, date] = DEFAULT_TEST_RANGE,
) -> dict:
    """End-to-end preparation: load raw orders, filter cancellations, split, and validate."""
    orders = load_orders(path)
    delivered, cancelled = separate_cancellations(orders)
    splits = split_delivered_orders(delivered, train_range, val_range, test_range)
    stats = validate_splits(splits)

    total = len(orders)
    cancellation_rate = round(len(cancelled) / total * 100, 2) if total else 0.0

    return {
        "splits": splits,
        "cancelled": cancelled,
        "summary": {
            "total_orders": total,
            "delivered_orders": len(delivered),
            "cancelled_orders": len(cancelled),
            "cancellation_rate_pct": cancellation_rate,
            "split_stats": stats,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate dataset and display split statistics.")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data/orders_2026_jan_aug.json"),
        help="Path to orders JSON file.",
    )
    args = parser.parse_args()

    result = prepare_dataset(args.data)
    summary = result["summary"]

    print("=== Dataset Summary ===")
    print(f"Total Orders:     {summary['total_orders']:,}")
    print(f"Delivered Orders: {summary['delivered_orders']:,}")
    print(f"Cancelled Orders: {summary['cancelled_orders']:,} ({summary['cancellation_rate_pct']}%)")
    print("\n=== Split Breakdown ===")
    for split_name, s in summary["split_stats"].items():
        print(f"Split: {split_name.upper():5s} | Count: {s['count']:,} | "
              f"Mean ETA: {s['mean_duration']:.2f}m | Median: {s['median_duration']:.2f}m | "
              f"Range: [{s['min_duration']:.2f}m, {s['max_duration']:.2f}m]")
        print(f"       Window: {s['first_confirmed'][:19]} -> {s['last_confirmed'][:19]}")


if __name__ == "__main__":
    main()

