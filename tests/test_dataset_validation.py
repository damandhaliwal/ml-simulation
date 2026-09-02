import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from prep.dataset_validation import (
    DEFAULT_TEST_RANGE,
    DEFAULT_TRAIN_RANGE,
    DEFAULT_VAL_RANGE,
    MARKET_TIMEZONE,
    load_orders,
    prepare_dataset,
    separate_cancellations,
    split_delivered_orders,
    validate_splits,
)


class TestDatasetValidation(unittest.TestCase):
    def _make_order(
        self,
        order_id: str,
        dt_local: datetime,
        status: str = "delivered",
        duration: float | None = 30.0,
    ) -> dict:
        dt_utc = dt_local.astimezone(timezone.utc)
        delivered_at = (
            (dt_utc + timedelta(minutes=duration)).isoformat()
            if status == "delivered" and duration is not None
            else None
        )
        return {
            "order_id": order_id,
            "confirmed_at": dt_utc.isoformat(),
            "status": status,
            "delivery_duration_minutes": duration if status == "delivered" else None,
            "delivered_at": delivered_at,
        }

    def test_load_orders(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "orders.json"
            with self.assertRaises(FileNotFoundError):
                load_orders(file_path)

            file_path.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_orders(file_path)

            sample = [{"order_id": "O001"}]
            file_path.write_text(json.dumps(sample), encoding="utf-8")
            loaded = load_orders(file_path)
            self.assertEqual(loaded, sample)

    def test_separate_cancellations(self):
        dt = datetime(2026, 1, 15, 12, tzinfo=MARKET_TIMEZONE)
        orders = [
            self._make_order("O001", dt, status="delivered", duration=25.0),
            self._make_order("O002", dt, status="cancelled", duration=None),
        ]
        delivered, cancelled = separate_cancellations(orders)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(delivered[0]["order_id"], "O001")
        self.assertEqual(cancelled[0]["order_id"], "O002")

        # Delivered order with missing duration is invalid
        invalid_delivered = [self._make_order("O003", dt, status="delivered", duration=None)]
        with self.assertRaises(ValueError):
            separate_cancellations(invalid_delivered)

        # Cancelled order with non-null duration is invalid
        invalid_cancelled = [{
            "order_id": "O004",
            "confirmed_at": dt.isoformat(),
            "status": "cancelled",
            "delivery_duration_minutes": 20.0,
        }]
        with self.assertRaises(ValueError):
            separate_cancellations(invalid_cancelled)

    def test_split_delivered_orders(self):
        train_order = self._make_order("O_TR", datetime(2026, 3, 15, 12, tzinfo=MARKET_TIMEZONE))
        val_order = self._make_order("O_VL", datetime(2026, 7, 10, 18, tzinfo=MARKET_TIMEZONE))
        test_order = self._make_order("O_TS", datetime(2026, 8, 20, 20, tzinfo=MARKET_TIMEZONE))

        splits = split_delivered_orders([train_order, val_order, test_order])
        self.assertEqual([o["order_id"] for o in splits["train"]], ["O_TR"])
        self.assertEqual([o["order_id"] for o in splits["val"]], ["O_VL"])
        self.assertEqual([o["order_id"] for o in splits["test"]], ["O_TS"])

        # Order outside of any split range
        outside_order = self._make_order("O_OUT", datetime(2026, 10, 1, 12, tzinfo=MARKET_TIMEZONE))
        with self.assertRaises(ValueError):
            split_delivered_orders([outside_order])

        # Overlapping or inverted split ranges
        with self.assertRaises(ValueError):
            split_delivered_orders(
                [train_order],
                train_range=(date(2026, 6, 1), date(2026, 1, 1)),
            )
        with self.assertRaises(ValueError):
            split_delivered_orders(
                [train_order],
                train_range=(date(2026, 1, 1), date(2026, 7, 15)),
                val_range=(date(2026, 7, 1), date(2026, 7, 31)),
            )

    def test_validate_splits_and_leakage_detection(self):
        train_order = self._make_order("O_TR", datetime(2026, 6, 30, 23, 50, tzinfo=MARKET_TIMEZONE), duration=20.0)
        val_order = self._make_order("O_VL", datetime(2026, 7, 1, 0, 10, tzinfo=MARKET_TIMEZONE), duration=30.0)
        test_order = self._make_order("O_TS", datetime(2026, 8, 1, 0, 5, tzinfo=MARKET_TIMEZONE), duration=40.0)

        valid_splits = {
            "train": [train_order],
            "val": [val_order],
            "test": [test_order],
        }
        stats = validate_splits(valid_splits)
        self.assertEqual(stats["train"]["count"], 1)
        self.assertEqual(stats["train"]["mean_duration"], 20.0)
        self.assertEqual(stats["val"]["count"], 1)
        self.assertEqual(stats["val"]["mean_duration"], 30.0)
        self.assertEqual(stats["test"]["count"], 1)
        self.assertEqual(stats["test"]["mean_duration"], 40.0)

        # Duplicate ID across splits
        dup_splits = {
            "train": [train_order],
            "val": [train_order],
            "test": [test_order],
        }
        with self.assertRaises(ValueError):
            validate_splits(dup_splits)

        # Chronological leakage: train order postdates val order
        leaky_splits = {
            "train": [self._make_order("O_TR2", datetime(2026, 7, 2, 12, tzinfo=MARKET_TIMEZONE))],
            "val": [val_order],
            "test": [test_order],
        }
        with self.assertRaises(ValueError):
            validate_splits(leaky_splits)

    def test_prepare_dataset_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "orders.json"
            orders = [
                self._make_order("O1", datetime(2026, 2, 1, 10, tzinfo=MARKET_TIMEZONE), duration=25.0),
                self._make_order("O2", datetime(2026, 7, 5, 12, tzinfo=MARKET_TIMEZONE), duration=35.0),
                self._make_order("O3", datetime(2026, 8, 10, 14, tzinfo=MARKET_TIMEZONE), duration=45.0),
                self._make_order("O4", datetime(2026, 4, 1, 12, tzinfo=MARKET_TIMEZONE), status="cancelled", duration=None),
            ]
            file_path.write_text(json.dumps(orders), encoding="utf-8")

            res = prepare_dataset(file_path)
            self.assertEqual(res["summary"]["total_orders"], 4)
            self.assertEqual(res["summary"]["delivered_orders"], 3)
            self.assertEqual(res["summary"]["cancelled_orders"], 1)
            self.assertEqual(res["summary"]["cancellation_rate_pct"], 25.0)
            self.assertEqual(len(res["splits"]["train"]), 1)
            self.assertEqual(len(res["splits"]["val"]), 1)
            self.assertEqual(len(res["splits"]["test"]), 1)


if __name__ == "__main__":
    unittest.main()

