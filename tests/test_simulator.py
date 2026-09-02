import json
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from simulator.generate_orders import MARKET_TIMEZONE, ZONES, generate_orders


class TestGenerateOrders(unittest.TestCase):
    def test_default_batch_covers_every_day_from_january_through_august(self):
        orders = generate_orders()
        observed_dates = {
            datetime.fromisoformat(order["confirmed_at"]).astimezone(MARKET_TIMEZONE).date()
            for order in orders
        }
        expected_dates = {date(2026, 1, 1) + timedelta(days=i) for i in range(243)}
        self.assertEqual(observed_dates, expected_dates)
        self.assertEqual(len({order["order_id"] for order in orders}), len(orders))

    def test_rows_contain_only_the_five_input_fields(self):
        fields = {
            "order_id", "confirmed_at", "pickup_zone_id", "dropoff_zone_id", "distance_km"
        }
        for order in generate_orders(date(2026, 1, 1), date(2026, 1, 1)):
            self.assertEqual(set(order), fields)

    def test_seed_controls_output(self):
        start = end = date(2026, 1, 1)
        orders = generate_orders(start, end, seed=42)
        self.assertEqual(orders, generate_orders(start, end, seed=42))
        self.assertNotEqual(orders, generate_orders(start, end, seed=43))
        self.assertEqual(orders, generate_orders(start, date(2026, 1, 2))[: len(orders)])

    def test_timestamps_are_utc_with_one_to_five_minute_gaps(self):
        for day in (date(2026, 1, 1), date(2026, 3, 8), date(2026, 11, 1)):
            with self.subTest(day=day):
                previous = datetime.combine(day, time.min, MARKET_TIMEZONE).astimezone(
                    timezone.utc
                )
                stop_at = datetime.combine(
                    day + timedelta(days=1), time.min, MARKET_TIMEZONE
                ).astimezone(timezone.utc)
                orders = generate_orders(day, day)
                self.assertTrue(orders)
                for order in orders:
                    current = datetime.fromisoformat(order["confirmed_at"])
                    self.assertEqual(current.utcoffset(), timedelta(0))
                    self.assertGreaterEqual(current - previous, timedelta(minutes=1))
                    self.assertLessEqual(current - previous, timedelta(minutes=5))
                    self.assertLess(current, stop_at)
                    self.assertEqual(current.astimezone(MARKET_TIMEZONE).date(), day)
                    previous = current
                self.assertLessEqual(stop_at - previous, timedelta(minutes=5))

    def test_spring_clock_change_skips_the_nonexistent_local_hour(self):
        orders = generate_orders(date(2026, 3, 8), date(2026, 3, 8))
        local_hours = {
            datetime.fromisoformat(order["confirmed_at"]).astimezone(MARKET_TIMEZONE).hour
            for order in orders
        }
        self.assertEqual(local_hours, set(range(24)) - {2})

    def test_distances_match_zone_rules(self):
        trip_types = set()
        for order in generate_orders(date(2026, 1, 1), date(2026, 1, 1)):
            self.assertIn(order["pickup_zone_id"], ZONES)
            self.assertIn(order["dropoff_zone_id"], ZONES)
            same_zone = order["pickup_zone_id"] == order["dropoff_zone_id"]
            trip_types.add(same_zone)
            low, high = (0.3, 2.0) if same_zone else (1.0, 5.0)
            self.assertGreaterEqual(order["distance_km"], low)
            self.assertLessEqual(order["distance_km"], high)
        self.assertEqual(trip_types, {True, False})

    def test_global_random_state_is_unchanged(self):
        before = random.getstate()
        generate_orders(date(2026, 1, 1), date(2026, 1, 1))
        self.assertEqual(random.getstate(), before)

    def test_reversed_date_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "end_date must be on or after start_date"):
            generate_orders(date(2026, 2, 1), date(2026, 1, 1))

    def test_script_saves_the_default_batch_as_json(self):
        script = (
            Path(__file__).resolve().parents[1] / "code" / "simulator" / "generate_orders.py"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(script)], cwd=directory,
                capture_output=True, text=True, check=True
            )
            output = Path(directory) / "data" / "orders_2026_jan_aug.json"
            with output.open(encoding="utf-8") as file:
                saved_orders = json.load(file)
            self.assertEqual(saved_orders, generate_orders())
        self.assertEqual(
            result.stdout,
            f"Generated {len(saved_orders):,} orders: data/orders_2026_jan_aug.json\n",
        )
        self.assertEqual(result.stderr, "")
