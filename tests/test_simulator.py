import json
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from simulator.generate_orders import (
    MARKET_TIMEZONE, OUTCOME_FIELDS, ZONES, _gap, _schedule,
    advance_market, create_market, generate_order, generate_orders,
)

AT = datetime(2026, 1, 1, 18, tzinfo=MARKET_TIMEZONE)
CLEAR = dict(weather_type="clear", traffic_index=1.0)
FIELDS = {
    "order_id", "order_group_id", "customer_id", "restaurant_id", "service_area_id",
    "pickup_zone_id", "dropoff_zone_id", "distance_km", "confirmed_at",
    "promised_delivery_at", "restaurant_backlog", "idle_couriers", "busy_couriers",
    "orders_waiting_for_courier", "basket_value_cad", "item_count", "traffic_index",
    "weather_type", "temperature_c", "precipitation_mm_per_hour", "local_hour",
    "day_of_week", "is_weekend", "is_public_holiday", "holiday_name",
    "is_long_weekend", "special_event", "status", *OUTCOME_FIELDS,
}


def small_market(**options):
    defaults = dict(zones=("Z1",), restaurants_per_zone=1, customers_per_zone=1,
                    couriers_per_zone=1, cancellation_probability=0)
    return create_market(**(defaults | options))


class TestOrderSnapshots(unittest.TestCase):
    def test_all_fields_are_present_without_future_outcomes(self):
        market = small_market()
        first = generate_order(AT, market, **CLEAR)
        self.assertEqual(set(first), FIELDS)
        self.assertEqual(len(FIELDS), 37)
        self.assertEqual(first["status"], "active")
        self.assertTrue(all(first[key] is None for key in OUTCOME_FIELDS))
        advance_market(market, AT + timedelta(hours=2))
        delivered = market["orders"][first["order_id"]]
        self.assertEqual(delivered["status"], "delivered")
        self.assertEqual(first["status"], "active")
        for key in FIELDS - {"status", *OUTCOME_FIELDS}:
            self.assertEqual(first[key], delivered[key])
        first["distance_km"] = 999
        self.assertNotEqual(delivered["distance_km"], 999)

    def test_counts_come_from_shared_state_and_exclude_the_new_order(self):
        market = small_market(max_orders_per_run=1)
        first = generate_order(AT, market, **CLEAR)
        second = generate_order(AT, market, **CLEAR)
        third = generate_order(AT, market, **CLEAR)
        self.assertEqual([o["restaurant_backlog"] for o in (first, second, third)], [0, 1, 2])
        self.assertEqual([o["orders_waiting_for_courier"] for o in (first, second, third)], [0, 0, 1])
        self.assertEqual((first["idle_couriers"], first["busy_couriers"]), (1, 0))
        self.assertEqual((second["idle_couriers"], second["busy_couriers"]), (0, 1))

    def test_no_couriers_leaves_ready_orders_active_and_unlabelled(self):
        market = small_market(couriers_per_zone=0)
        generate_order(AT, market, **CLEAR)
        generate_order(AT, market, **CLEAR)
        advance_market(market, AT + timedelta(hours=2))
        third = generate_order(AT + timedelta(hours=2), market, **CLEAR)
        self.assertEqual(third["restaurant_backlog"], 0)
        self.assertEqual(third["orders_waiting_for_courier"], 2)
        for order in market["orders"].values():
            self.assertEqual(order["status"], "active")
            self.assertIsNone(order["picked_up_at"])
            self.assertIsNone(order["delivery_duration_minutes"])
            self.assertIsNone(order["late_delivery"])

    def test_calendar_uses_local_dates_and_explicit_calendars(self):
        holidays = {date(2026, 1, 2): "Toy Friday", date(2026, 1, 12): "Toy Monday"}
        market = small_market(holidays=holidays, special_events={date(2026, 1, 3): "festival"})
        for day_number in range(1, 14):
            at = datetime(2026, 1, day_number, 23, 30, tzinfo=MARKET_TIMEZONE)
            order = generate_order(at, market, **CLEAR)
            self.assertEqual(order["local_hour"], 23)
            self.assertEqual(order["day_of_week"], at.weekday())
            self.assertEqual(order["is_weekend"], at.weekday() >= 5)
            self.assertEqual(order["is_public_holiday"], at.date() in holidays)
            self.assertEqual(order["is_long_weekend"], day_number in (2, 3, 4, 10, 11, 12))
            self.assertEqual(order["holiday_name"], holidays.get(at.date()))
            self.assertEqual(order["special_event"], "festival" if day_number == 3 else None)

    def test_overrides_are_exact_and_labels_do_not_change_traffic(self):
        market = small_market()
        order = generate_order(AT, market, traffic_index=1.8, weather_type="rain",
                               temperature_c=4, precipitation_mm_per_hour=8,
                               holiday_name="Toy holiday", special_event="sports",
                               item_count=8)
        for key, value in dict(traffic_index=1.8, weather_type="rain", temperature_c=4,
                               precipitation_mm_per_hour=8, item_count=8,
                               holiday_name="Toy holiday", special_event="sports").items():
            self.assertEqual(order[key], value)
        plain = generate_order(AT, small_market(), **CLEAR)
        holiday = generate_order(AT, small_market(), **CLEAR, holiday_name="Toy holiday")
        self.assertEqual(plain["traffic_index"], holiday["traffic_index"])

    def test_hourly_weather_is_shared_and_future_conditions_do_not_rewrite_inputs(self):
        market = small_market()
        first = generate_order(AT, market)
        second = generate_order(AT + timedelta(seconds=1), market)
        keys = ("traffic_index", "weather_type", "temperature_c", "precipitation_mm_per_hour")
        self.assertEqual([first[k] for k in keys], [second[k] for k in keys])
        generate_order(AT + timedelta(seconds=2), market, traffic_index=3, weather_type="snow")
        self.assertEqual([first[k] for k in keys],
                         [market["orders"][first["order_id"]][k] for k in keys])

    def test_group_add_on_reuses_customer_not_restaurant(self):
        market = small_market(restaurants_per_zone=2)
        first = generate_order(AT, market, **CLEAR)
        second = generate_order(AT, market, **CLEAR, order_group_id=first["order_group_id"])
        self.assertEqual(first["customer_id"], second["customer_id"])
        self.assertEqual(first["order_group_id"], second["order_group_id"])
        self.assertNotEqual(first["restaurant_id"], second["restaurant_id"])
        with self.assertRaisesRegex(ValueError, "every restaurant"):
            generate_order(AT, market, order_group_id=first["order_group_id"])

    def test_invalid_calls_do_not_add_orders_or_advance_time(self):
        options = [
            {"traffic_index": 0}, {"traffic_index": float("nan")},
            {"weather_type": "unknown"}, {"weather_type": ""},
            {"weather_type": "clear", "precipitation_mm_per_hour": 1},
            {"weather_type": "snow", "temperature_c": 10},
            {"weather_type": "rain", "temperature_c": -5},
            {"weather_type": "rain", "precipitation_mm_per_hour": -1},
            {"temperature_c": 10}, {"item_count": 0}, {"item_count": 1.5},
            {"item_count": True}, {"prep_time_multiplier": float("inf")},
            {"prep_time_multiplier": -1}, {"holiday_name": ""},
            {"order_group_id": "missing"},
        ]
        for kwargs in options:
            with self.subTest(kwargs=kwargs):
                market = small_market()
                with self.assertRaises(ValueError):
                    generate_order(AT, market, **kwargs)
                self.assertEqual(market["orders"], {})
                self.assertIsNone(market["now"])
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            generate_order(AT.replace(tzinfo=None), small_market())
        market = small_market()
        generate_order(AT, market)
        with self.assertRaisesRegex(ValueError, "backward"):
            generate_order(AT - timedelta(seconds=1), market)
        self.assertEqual(len(market["orders"]), 1)


class TestLifecycle(unittest.TestCase):
    def test_live_time_increments_match_one_advance(self):
        markets = [small_market(cancellation_probability=0.5) for _ in range(2)]
        for market in markets:
            for _ in range(5):
                generate_order(AT, market, **CLEAR)
        advance_market(markets[0], AT + timedelta(hours=3))
        for minute in range(181):
            advance_market(markets[1], AT + timedelta(minutes=minute))
        for key in ("orders", "runs", "assignments", "events", "couriers"):
            self.assertEqual(markets[0][key], markets[1][key])

    def test_new_traffic_affects_future_legs_without_rewriting_old_features(self):
        outcomes = []
        for traffic in (1, 3):
            market = small_market(max_orders_per_run=1)
            generate_order(AT, market, **CLEAR)
            generate_order(AT + timedelta(minutes=1), market,
                           weather_type="clear", traffic_index=traffic)
            advance_market(market, AT + timedelta(hours=4))
            outcomes.append(market["orders"]["O000001"])
        self.assertEqual([o["traffic_index"] for o in outcomes], [1, 1])
        self.assertEqual(outcomes[0]["ready_at"], outcomes[1]["ready_at"])
        self.assertGreater(outcomes[1]["delivery_duration_minutes"],
                           outcomes[0]["delivery_duration_minutes"])

    def test_prep_and_assignment_overlap_and_labels_use_endpoints(self):
        market = small_market(promise_minutes=1)
        snapshot = generate_order(AT, market, **CLEAR, item_count=2)
        advance_market(market, AT + timedelta(hours=2))
        order = market["orders"][snapshot["order_id"]]
        stamps = [datetime.fromisoformat(order[key]) for key in
                  ("confirmed_at", "prep_started_at", "ready_at", "picked_up_at", "delivered_at")]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(market["assignments"][0]["assigned_at"], order["confirmed_at"])
        self.assertEqual(order["delivery_duration_minutes"], (stamps[-1] - stamps[0]).total_seconds() / 60)
        self.assertTrue(order["late_delivery"])
        self.assertEqual(datetime.fromisoformat(order["promised_delivery_at"]) - stamps[0],
                         timedelta(minutes=1))

    def test_traffic_and_rain_slow_travel_not_prep(self):
        results = []
        for options in (CLEAR, dict(weather_type="clear", traffic_index=2),
                        dict(weather_type="rain", traffic_index=1)):
            market = small_market()
            snapshot = generate_order(AT, market, **options)
            advance_market(market, AT + timedelta(hours=4))
            results.append(market["orders"][snapshot["order_id"]])
        self.assertEqual(len({o["ready_at"] for o in results}), 1)
        self.assertGreater(results[1]["delivery_duration_minutes"], results[0]["delivery_duration_minutes"])
        self.assertGreater(results[2]["delivery_duration_minutes"], results[0]["delivery_duration_minutes"])

    def test_prep_multiplier_changes_only_preparation_duration_directly(self):
        durations = []
        for multiplier in (1, 2):
            market = small_market()
            generate_order(AT, market, **CLEAR, prep_time_multiplier=multiplier)
            advance_market(market, AT + timedelta(hours=4))
            order = market["orders"]["O000001"]
            durations.append((datetime.fromisoformat(order["ready_at"]) -
                              datetime.fromisoformat(order["prep_started_at"])).total_seconds())
        self.assertAlmostEqual(durations[1], 2 * durations[0], places=5)

    def test_cancellation_frees_prep_and_does_not_resurrect_stale_events(self):
        market = small_market()
        first = generate_order(AT, market, **CLEAR, prep_time_multiplier=10)
        second = generate_order(AT, market, **CLEAR)
        cancel_at = AT + timedelta(minutes=1)
        _schedule(market, cancel_at.astimezone(timezone.utc), "cancel", first["order_id"])
        advance_market(market, AT + timedelta(hours=8))
        cancelled, delivered = (market["orders"][o["order_id"]] for o in (first, second))
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNotNone(cancelled["prep_started_at"])
        for key in ("ready_at", "picked_up_at", "delivered_at", "delivery_duration_minutes", "late_delivery"):
            self.assertIsNone(cancelled[key])
        self.assertEqual(delivered["prep_started_at"], cancel_at.astimezone(timezone.utc).isoformat())
        self.assertEqual(delivered["status"], "delivered")
        self.assertFalse(market["active"])
        self.assertTrue(all(c["run_id"] is None for c in market["couriers"].values()))

    def test_probability_one_attempts_cancel_but_never_cancels_after_pickup(self):
        market = small_market(cancellation_probability=1)
        for _ in range(5):
            generate_order(AT, market, **CLEAR, prep_time_multiplier=100)
        advance_market(market, AT + timedelta(hours=48))
        self.assertEqual({o["status"] for o in market["orders"].values()}, {"cancelled"})
        self.assertEqual(len(market["assigned"]), 0)
        market = small_market()
        generate_order(AT, market, **CLEAR, prep_time_multiplier=0.01)
        advance_market(market, AT + timedelta(minutes=5))
        self.assertIsNotNone(market["orders"]["O000001"]["picked_up_at"])
        _schedule(market, (AT + timedelta(minutes=6)).astimezone(timezone.utc), "cancel", "O000001")
        advance_market(market, AT + timedelta(hours=1))
        self.assertEqual(market["orders"]["O000001"]["status"], "delivered")

    def test_ready_milestone_is_preserved_on_cancellation(self):
        market = small_market(cancellation_probability=1)
        generate_order(AT, market, weather_type="clear", traffic_index=100,
                       prep_time_multiplier=0.01)
        advance_market(market, AT + timedelta(hours=48))
        order = market["orders"]["O000001"]
        self.assertEqual(order["status"], "cancelled")
        self.assertLess(order["ready_at"], order["cancelled_at"])
        self.assertIsNone(order["picked_up_at"])
        self.assertIsNone(order["late_delivery"])


class TestBatching(unittest.TestCase):
    def test_capacity_plans_and_stop_order(self):
        market = small_market(max_orders_per_run=3)
        snapshots = [generate_order(AT, market, **CLEAR) for _ in range(4)]
        run = market["runs"]["RUN000001"]
        self.assertEqual(len(run["order_ids"]), 3)
        self.assertEqual(len(run["plan_revisions"][0]["stops"]), 2)
        self.assertEqual(len(run["plan_revisions"][-1]["stops"]), 6)
        self.assertEqual(len(market["waiting"]), 1)
        self.assertEqual(len({o["order_group_id"] for o in snapshots}), 4)
        advance_market(market, AT + timedelta(hours=8))
        for run in market["runs"].values():
            self.assertLessEqual(len(run["order_ids"]), 3)
            completed = [s for s in run["stops"] if s["status"] == "completed"]
            self.assertEqual([s["completed_at"] for s in completed],
                             sorted(s["completed_at"] for s in completed))
            for order_id in run["order_ids"]:
                stops = [s for s in completed if s["order_id"] == order_id]
                self.assertEqual([s["kind"] for s in stops], ["pickup", "dropoff"])

    def test_both_gaps_must_be_near_the_first_order(self):
        for far_kind in ("pickup", "dropoff"):
            with self.subTest(far_kind=far_kind):
                market = small_market(max_orders_per_run=3)
                generate_order(AT, market, **CLEAR)
                def gap(market, first, other, kind):
                    if first["order_id"] == "O000001" and other["order_id"] == "O000003" and kind == far_kind:
                        return 1.01
                    return 0.1
                with patch("simulator.generate_orders._gap", side_effect=gap):
                    generate_order(AT, market, **CLEAR)
                    generate_order(AT, market, **CLEAR)
                self.assertEqual(market["runs"]["RUN000001"]["order_ids"], ["O000001", "O000002"])
                self.assertIn("O000003", market["waiting"])

    def test_gap_is_retained_and_symmetric(self):
        market = create_market(cancellation_probability=0)
        orders = [generate_order(AT, market, **CLEAR) for _ in range(10)]
        for first, other in zip(orders, orders[1:]):
            for kind in ("pickup", "dropoff"):
                gap = _gap(market, first, other, kind)
                before = market["rng"].getstate()
                self.assertEqual(gap, _gap(market, other, first, kind))
                self.assertEqual(before, market["rng"].getstate())


class TestHistoricalGeneration(unittest.TestCase):
    def test_dates_required_and_input_validation(self):
        with self.assertRaises(TypeError):
            generate_orders()
        for start, end in ((date(2026, 2, 1), date(2026, 1, 1)), (AT, AT)):
            with self.assertRaises(ValueError):
                generate_orders(start, end)
        for rate in (0, -1, float("inf"), float("nan"), 1e30):
            with self.assertRaises(ValueError):
                generate_orders(date(2026, 1, 1), date(2026, 1, 1), orders_per_hour=rate)
        for options in ({"couriers_per_zone": -1}, {"restaurants_per_zone": 0},
                        {"customers_per_zone": 0}, {"max_orders_per_run": 0},
                        {"batch_max_gap_km": -1}, {"promise_minutes": 0},
                        {"cancellation_probability": 1.1}, {"zones": ("Z1", "Z1")},
                        {"holidays": {"2026-01-01": "bad key"}}):
            with self.assertRaises(ValueError):
                create_market(**options)

    def test_seed_determinism_and_global_random_is_unchanged(self):
        before = random.getstate()
        day = date(2026, 1, 1)
        first = generate_orders(day, day)
        self.assertEqual(first, generate_orders(day, day))
        self.assertNotEqual(first, generate_orders(day, day, seed=43))
        self.assertEqual(before, random.getstate())

    def test_january_to_august_days_and_daylight_saving_boundaries(self):
        for day in ([date(2026, month, 1) for month in range(1, 9)] +
                    [date(2026, 3, 8), date(2026, 11, 1), date(2027, 1, 1)]):
            with self.subTest(day=day):
                start = datetime.combine(day, time.min, MARKET_TIMEZONE).astimezone(timezone.utc)
                stop = datetime.combine(day + timedelta(days=1), time.min, MARKET_TIMEZONE).astimezone(timezone.utc)
                orders = generate_orders(day, day)
                self.assertTrue(orders)
                previous = start
                for order in orders:
                    at = datetime.fromisoformat(order["confirmed_at"])
                    self.assertEqual(at.utcoffset(), timedelta(0))
                    self.assertEqual(at.astimezone(MARKET_TIMEZONE).date(), day)
                    self.assertGreaterEqual(at - previous, timedelta(minutes=1))
                    self.assertLessEqual(at - previous, timedelta(minutes=5))
                    self.assertLess(at, stop)
                    for key in ("prep_started_at", "ready_at", "picked_up_at", "delivered_at", "cancelled_at"):
                        if order[key] is not None:
                            self.assertLessEqual(datetime.fromisoformat(order[key]), stop)
                    previous = at
                if day == date(2026, 3, 8):
                    self.assertEqual({o["local_hour"] for o in orders}, set(range(24)) - {2})

    def test_identities_distances_and_terminal_invariants(self):
        market = create_market()
        orders = generate_orders(date(2026, 1, 1), date(2026, 1, 2), market=market)
        self.assertEqual(len({o["order_id"] for o in orders}), len(orders))
        self.assertEqual({o["status"] for o in orders}, {"active", "delivered", "cancelled"})
        for order in orders:
            self.assertEqual(set(order), FIELDS)
            self.assertIn(order["pickup_zone_id"], ZONES)
            self.assertEqual(market["restaurants"][order["restaurant_id"]]["zone_id"], order["pickup_zone_id"])
            self.assertEqual(market["customers"][order["customer_id"]], order["dropoff_zone_id"])
            low, high = (0.3, 2.0) if order["pickup_zone_id"] == order["dropoff_zone_id"] else (1.0, 5.0)
            self.assertTrue(low <= order["distance_km"] <= high)
            if order["status"] == "delivered":
                self.assertIsNone(order["cancelled_at"])
                self.assertIsNotNone(order["delivery_duration_minutes"])
                self.assertIsInstance(order["late_delivery"], bool)
            else:
                self.assertIsNone(order["delivery_duration_minutes"])
                self.assertIsNone(order["late_delivery"])
        for assignment in market["assignments"]:
            self.assertLessEqual(assignment["pickup_gap_km"], 1)
            self.assertLessEqual(assignment["dropoff_gap_km"], 1)
        for run in market["runs"].values():
            self.assertLessEqual(len(run["order_ids"]), 2)
            for stop in run["stops"]:
                if market["orders"][stop["order_id"]]["status"] == "cancelled":
                    self.assertEqual(stop["status"], "cancelled")

    def test_rate_supply_and_existing_market_controls(self):
        day = date(2026, 1, 1)
        quiet = generate_orders(day, day, orders_per_hour=10)
        busy = generate_orders(day, day, orders_per_hour=40)
        self.assertGreater(len(busy), 3 * len(quiet))
        market = small_market(couriers_per_zone=0)
        first = generate_orders(day, day, market=market, orders_per_hour=1)
        second = generate_orders(day + timedelta(days=1), day + timedelta(days=1),
                                 market=market, orders_per_hour=1)
        self.assertNotEqual(first[0]["order_id"], second[0]["order_id"])
        self.assertEqual(second[0]["orders_waiting_for_courier"], len(first))
        self.assertTrue(all(o["status"] == "active" for o in first + second))

    def test_script_saves_explicit_date_cohort_as_json(self):
        script = Path(__file__).resolve().parents[1] / "code" / "simulator" / "generate_orders.py"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(script), "--start", "2026-01-01", "--end", "2026-01-01",
                 "--output", "data/orders.json", "--orders-per-hour", "2"],
                cwd=directory, capture_output=True, text=True, check=True,
            )
            with (Path(directory) / "data" / "orders.json").open(encoding="utf-8") as file:
                saved = json.load(file)
        self.assertEqual(saved, generate_orders(date(2026, 1, 1), date(2026, 1, 1), orders_per_hour=2))
        self.assertEqual(result.stdout, f"Generated {len(saved):,} orders: data/orders.json\n")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
