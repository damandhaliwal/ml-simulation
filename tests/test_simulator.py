import json
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from simulator.generate_orders import MARKET_TIMEZONE, ZONES, generate_order, generate_orders

DAY = date(2026, 1, 1)
AT = datetime(2026, 1, 1, 18, tzinfo=MARKET_TIMEZONE)
BASE = dict(weather_type="clear", traffic_index=1, cancellation_probability=0, batch_probability=0)
FIELDS = {
    "order_id", "order_group_id", "customer_id", "restaurant_id", "service_area_id",
    "pickup_zone_id", "dropoff_zone_id", "distance_km", "confirmed_at",
    "promised_delivery_at", "restaurant_backlog", "idle_couriers", "busy_couriers",
    "orders_waiting_for_courier", "basket_value_cad", "item_count", "traffic_index",
    "weather_type", "temperature_c", "precipitation_mm_per_hour", "local_hour",
    "day_of_week", "is_weekend", "is_public_holiday", "holiday_name", "is_long_weekend",
    "special_event", "status", "delivered_at", "delivery_duration_minutes", "late_delivery",
}


class TestGenerateOrder(unittest.TestCase):
    def test_complete_row_without_intermediate_stages(self):
        order = generate_order(AT, **BASE)
        self.assertEqual(set(order), FIELDS)
        self.assertEqual(len(order), 31)
        self.assertEqual(order["status"], "delivered")
        confirmed, delivered, promised = (
            datetime.fromisoformat(order[key])
            for key in ("confirmed_at", "delivered_at", "promised_delivery_at")
        )
        self.assertAlmostEqual((delivered - confirmed).total_seconds() / 60,
                               order["delivery_duration_minutes"])
        self.assertEqual(order["late_delivery"], delivered > promised)
        self.assertEqual(promised - confirmed, timedelta(minutes=45))

    def test_seed_reproducibility_and_no_shared_state(self):
        before = random.getstate()
        first = generate_order(AT, **BASE)
        self.assertEqual(first, generate_order(AT, **BASE))
        self.assertNotEqual(first, generate_order(AT, seed=43, **BASE))
        self.assertNotEqual(first, generate_order(AT, order_id="O000002", **BASE))
        self.assertEqual(random.getstate(), before)

    def test_scenario_overrides_and_calendar_rules(self):
        order = generate_order(AT, traffic_index=2, weather_type="rain",
                               temperature_c=4, precipitation_mm_per_hour=8,
                               item_count=6, holiday_name="Toy holiday", special_event="festival")
        for key, value in dict(traffic_index=2, weather_type="rain", temperature_c=4,
                               precipitation_mm_per_hour=8, item_count=6,
                               holiday_name="Toy holiday", special_event="festival").items():
            self.assertEqual(order[key], value)
        holidays = {date(2026, 1, 2): "Friday holiday", date(2026, 1, 12): "Monday holiday"}
        for day in range(1, 14):
            at = datetime(2026, 1, day, 23, 30, tzinfo=MARKET_TIMEZONE)
            row = generate_order(at, holidays=holidays,
                                 special_events={date(2026, 1, 3): "festival"})
            self.assertEqual(row["local_hour"], 23)
            self.assertEqual(row["day_of_week"], at.weekday())
            self.assertEqual(row["is_weekend"], at.weekday() >= 5)
            self.assertEqual(row["is_public_holiday"], at.date() in holidays)
            self.assertEqual(row["holiday_name"], holidays.get(at.date()))
            self.assertEqual(row["is_long_weekend"], day in (2, 3, 4, 10, 11, 12))
            self.assertEqual(row["special_event"], "festival" if day == 3 else None)

    def test_traffic_weather_preparation_and_basket_affect_duration(self):
        options = BASE | {"item_count": 2}
        baseline = generate_order(AT, **options)["delivery_duration_minutes"]
        for change in ({"traffic_index": 2}, {"weather_type": "rain"},
                       {"prep_time_multiplier": 2}, {"item_count": 6},
                       {"weather_type": "storm", "precipitation_mm_per_hour": 20}):
            with self.subTest(change=change):
                order = generate_order(AT, **(options | change))
                self.assertGreater(order["delivery_duration_minutes"], baseline)
        labelled = generate_order(AT, **options, holiday_name="Toy holiday", special_event="festival")
        self.assertEqual(labelled["delivery_duration_minutes"], baseline)

    def test_supply_counts_and_demand_relationships(self):
        normal = generate_order(AT, **BASE)
        busy = generate_order(AT, **BASE, orders_per_hour=60)
        self.assertGreaterEqual(busy["restaurant_backlog"], normal["restaurant_backlog"])
        self.assertGreaterEqual(busy["orders_waiting_for_courier"], normal["orders_waiting_for_courier"])
        self.assertGreaterEqual(busy["delivery_duration_minutes"], normal["delivery_duration_minutes"])
        empty = generate_order(AT, **BASE, couriers_per_zone=0)
        self.assertEqual((empty["idle_couriers"], empty["busy_couriers"]), (0, 0))
        self.assertGreaterEqual(empty["delivery_duration_minutes"], normal["delivery_duration_minutes"])
        for order in generate_orders(DAY, DAY):
            self.assertEqual(order["idle_couriers"] + order["busy_couriers"], 3)
            for key in ("restaurant_backlog", "orders_waiting_for_courier"):
                self.assertIsInstance(order[key], int)
                self.assertGreaterEqual(order[key], 0)

    def test_nearby_batch_delay_is_bounded_and_not_a_feature(self):
        for seed in range(20):
            plain = generate_order(AT, seed=seed, **BASE)
            batched = generate_order(AT, seed=seed, **(BASE | {"batch_probability": 1}))
            difference = batched["delivery_duration_minutes"] - plain["delivery_duration_minutes"]
            # At most two 1 km gaps at 4 min/km, plus two minutes for the extra stops.
            self.assertGreaterEqual(difference, 1.99)
            self.assertLessEqual(difference, 10.01)
            no_extra = generate_order(AT, seed=seed, max_orders_per_run=1,
                                      **(BASE | {"batch_probability": 1}))
            no_extra_again = generate_order(AT, seed=seed, max_orders_per_run=1, **BASE)
            self.assertEqual(no_extra, no_extra_again)
            self.assertNotIn("batch_size", batched)

    def test_cancelled_rows_have_missing_labels_and_promise_is_separate(self):
        for row in generate_orders(DAY, DAY, cancellation_probability=1):
            self.assertEqual(row["status"], "cancelled")
            for key in ("delivered_at", "delivery_duration_minutes", "late_delivery"):
                self.assertIsNone(row[key])
        early = generate_order(AT, promise_minutes=1, **BASE)
        generous = generate_order(AT, promise_minutes=1000, **BASE)
        self.assertEqual(early["delivery_duration_minutes"], generous["delivery_duration_minutes"])
        self.assertTrue(early["late_delivery"])
        self.assertFalse(generous["late_delivery"])

    def test_invalid_controls(self):
        bad_options = [
            {"traffic_index": 0}, {"traffic_index": float("nan")}, {"weather_type": "unknown"},
            {"weather_type": "clear", "precipitation_mm_per_hour": 2},
            {"weather_type": "snow", "temperature_c": 20},
            {"weather_type": "rain", "temperature_c": -5},
            {"item_count": 0}, {"item_count": True}, {"couriers_per_zone": -1},
            {"restaurants_per_zone": 0}, {"prep_time_multiplier": 0},
            {"cancellation_probability": 1.1}, {"batch_probability": -1},
            {"batch_max_gap_km": -1}, {"max_orders_per_run": 0}, {"promise_minutes": 0},
            {"zones": ("Z1", "Z1")}, {"order_id": ""}, {"holiday_name": ""},
            {"holidays": {"2026-01-01": "wrong date type"}},
            {"weather_multipliers": {"hail": 2.0}}, {"weather_multipliers": {"storm": 0}},
            {"weather_multipliers": {"storm": float("nan")}},
            {"weather_multipliers": {"storm": True}}, {"weather_multipliers": [("storm", 1.8)]},
        ]
        for options in bad_options:
            with self.subTest(options=options), self.assertRaises(ValueError):
                generate_order(AT, **options)
        with self.assertRaises(ValueError):
            generate_order(AT.replace(tzinfo=None))

    def test_weather_multiplier_override_touches_only_that_weather(self):
        at = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
        storm = generate_order(at, order_id="W-STORM", seed=5, weather_type="storm")
        storm_shifted = generate_order(at, order_id="W-STORM", seed=5, weather_type="storm",
                                       weather_multipliers={"storm": 1.8})
        self.assertGreater(storm_shifted["delivery_duration_minutes"],
                           storm["delivery_duration_minutes"])
        clear = generate_order(at, order_id="W-CLEAR", seed=5, weather_type="clear",
                               precipitation_mm_per_hour=0)
        clear_shifted = generate_order(at, order_id="W-CLEAR", seed=5, weather_type="clear",
                                       precipitation_mm_per_hour=0,
                                       weather_multipliers={"storm": 1.8})
        self.assertEqual(clear_shifted, clear)
        default = generate_order(at, order_id="W-DEF", seed=5,
                                 weather_multipliers={"storm": 1.5, "rain": 1.15,
                                                      "snow": 1.35, "clear": 1.0})
        self.assertEqual(default, generate_order(at, order_id="W-DEF", seed=5))


class TestGenerateOrders(unittest.TestCase):
    def test_batch_reproducibility_and_single_order_reuse(self):
        rows = generate_orders(DAY, DAY)
        self.assertEqual(rows, generate_orders(DAY, DAY))
        self.assertNotEqual(rows, generate_orders(DAY, DAY, seed=43))
        self.assertEqual(rows, generate_orders(DAY, DAY + timedelta(days=1))[:len(rows)])
        first = rows[0]
        self.assertEqual(first, generate_order(datetime.fromisoformat(first["confirmed_at"]),
                                              order_id=first["order_id"]))

    def test_date_window_utc_and_daylight_saving(self):
        for day in (DAY, date(2026, 3, 8), date(2026, 11, 1), date(2027, 8, 31)):
            start = datetime.combine(day, time.min, MARKET_TIMEZONE).astimezone(timezone.utc)
            stop = datetime.combine(day + timedelta(days=1), time.min, MARKET_TIMEZONE).astimezone(timezone.utc)
            rows = generate_orders(day, day, cancellation_probability=0)
            previous = start
            for row in rows:
                at = datetime.fromisoformat(row["confirmed_at"])
                self.assertEqual(at.utcoffset(), timedelta(0))
                self.assertEqual(at.astimezone(MARKET_TIMEZONE).date(), day)
                self.assertGreaterEqual(at - previous, timedelta(minutes=1))
                self.assertLessEqual(at - previous, timedelta(minutes=5))
                self.assertLess(at, stop)
                previous = at
            # These are complete synthetic outcomes, not censored at the end of the window.
            self.assertGreater(datetime.fromisoformat(rows[-1]["delivered_at"]), stop)
            if day == date(2026, 3, 8):
                self.assertEqual({row["local_hour"] for row in rows}, set(range(24)) - {2})

    def test_feature_ranges_and_identity_consistency(self):
        restaurants, customers = {}, {}
        for row in generate_orders(DAY, DAY):
            self.assertEqual(set(row), FIELDS)
            for identities, key, zone in ((restaurants, "restaurant_id", "pickup_zone_id"),
                                          (customers, "customer_id", "dropoff_zone_id")):
                self.assertEqual(identities.setdefault(row[key], row[zone]), row[zone])
                self.assertIn(row[zone], ZONES)
            low, high = (0.3, 2) if row["pickup_zone_id"] == row["dropoff_zone_id"] else (1, 5)
            self.assertTrue(low <= row["distance_km"] <= high)
            self.assertTrue(1 <= row["item_count"] <= 5)
            self.assertTrue(8 * row["item_count"] <= row["basket_value_cad"] <= 20 * row["item_count"])
            self.assertGreaterEqual(row["precipitation_mm_per_hour"], 0)

    def test_demand_dates_and_empty_window_validation(self):
        self.assertGreater(len(generate_orders(DAY, DAY, orders_per_hour=40)),
                           3 * len(generate_orders(DAY, DAY, orders_per_hour=10)))
        with self.assertRaises(TypeError):
            generate_orders()
        for start, end in ((DAY + timedelta(days=1), DAY), (AT, AT)):
            with self.assertRaises(ValueError):
                generate_orders(start, end)
        for rate in (0, -1, float("nan"), float("inf"), 1e30):
            with self.assertRaises(ValueError):
                generate_orders(DAY, DAY, orders_per_hour=rate)
        self.assertEqual(generate_orders(DAY, DAY, orders_per_hour=1e-20), [])
        with self.assertRaises(ValueError):
            generate_orders(DAY, DAY, orders_per_hour=1e-20, weather_type="unknown")

    def test_cli_exports_only_rows(self):
        script = Path(__file__).resolve().parents[1] / "code" / "simulator" / "generate_orders.py"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(script), "--start", str(DAY), "--end", str(DAY),
                 "--output", "data/orders.json"], cwd=directory,
                capture_output=True, text=True, check=True,
            )
            with (Path(directory) / "data" / "orders.json").open(encoding="utf-8") as file:
                rows = json.load(file)
        self.assertEqual(rows, generate_orders(DAY, DAY))
        self.assertEqual(result.stdout, f"Generated {len(rows):,} orders: data/orders.json\n")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
