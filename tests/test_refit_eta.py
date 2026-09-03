import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from models import lightgbm_eta as eta
from models.refit_eta import REFIT_TREES, feature_contract, file_sha256, load_artifact, refit_eta
from prep.dataset_validation import parse_timestamp
from simulator.generate_orders import generate_order


class TestRefitETA(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "orders.json"
        self.output = Path(self.temp.name) / "artifact"
        self.observed_at = datetime(2026, 9, 3, tzinfo=timezone.utc)
        self.orders = [
            generate_order(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=2*i),
                           order_id=f"REFIT-{i}", cancellation_probability=0)
            for i in range(120)
        ]
        # These late labels were excluded at earlier monthly cutoffs, not at refit time.
        for month, day in ((6, 30), (7, 31), (8, 31)):
            self.orders.append(generate_order(
                datetime.fromisoformat(f"2026-{month:02d}-{day}T23:59:00-04:00"),
                order_id=f"BOUNDARY-{month}", cancellation_probability=0,
            ))
        self.orders.append(generate_order(datetime(2026, 1, 2, tzinfo=timezone.utc),
                                          order_id="CANCELLED", cancellation_probability=1))
        self.data.write_text(json.dumps(self.orders), encoding="utf-8")

    def refit(self):
        return refit_eta(self.data, self.output, observed_at=self.observed_at)

    def test_full_refit_keeps_all_deliveries_and_roundtrips(self):
        original_fit = eta.LightGBMETAModel.fit
        with patch.object(eta.LightGBMETAModel, "fit", autospec=True, side_effect=original_fit) as fit:
            metadata = self.refit()
        fit.assert_called_once()
        fitted_rows = fit.call_args.args[1]
        self.assertEqual(fit.call_args.kwargs, {})  # No validation set or early stopping.
        self.assertEqual(fitted_rows, self.orders[:-1])
        self.assertEqual(metadata["training_rows"], 123)
        self.assertEqual(metadata["cancelled_rows_excluded"], 1)
        self.assertEqual(metadata["total_orders"], 124)
        self.assertEqual(metadata["roundtrip_verified_rows"], 123)
        self.assertEqual(metadata["tree_count"], REFIT_TREES)
        self.assertFalse(metadata["early_stopping_used"])
        self.assertEqual(metadata["source_sha256"], file_sha256(self.data))
        self.assertEqual(metadata["feature_contract"], feature_contract())
        loaded, saved = load_artifact(self.output)
        self.assertEqual(saved, metadata)
        self.assertEqual(loaded.model.booster_.num_trees(), 160)
        self.assertEqual(loaded.model.best_iteration_, 0)
        inputs = [{k: v for k, v in order.items() if k not in
                   ("status", "delivery_duration_minutes", "delivered_at", "late_delivery")}
                  for order in fitted_rows]
        self.assertEqual(loaded.predict(inputs), loaded.predict(fitted_rows))

    def test_requires_observed_labels_and_aware_cutoff(self):
        latest = max(parse_timestamp(o["delivered_at"]) for o in self.orders[:-1])
        for cutoff in (latest, latest - timedelta(seconds=1), datetime(2026, 9, 3)):
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                refit_eta(self.data, self.output, observed_at=cutoff)
            self.assertFalse(self.output.exists())

    def test_no_delivered_orders_is_an_error(self):
        self.data.write_text(json.dumps([self.orders[-1]]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "No delivered orders"):
            self.refit()
        self.assertFalse(self.output.exists())

    def test_existing_artifact_is_not_overwritten(self):
        self.refit()
        checksum = file_sha256(self.output / "model.joblib")
        with patch.object(eta.LightGBMETAModel, "fit") as fit:
            with self.assertRaises(FileExistsError):
                self.refit()
            fit.assert_not_called()
        self.assertEqual(checksum, file_sha256(self.output / "model.joblib"))

    def test_incompatible_metadata_rejected_before_deserialization(self):
        metadata = self.refit()
        for field, value in (("format_version", 99), ("feature_contract", {}), ("runtime_versions", {})):
            with self.subTest(field=field):
                changed = dict(metadata, **{field: value})
                (self.output / "metadata.json").write_text(json.dumps(changed), encoding="utf-8")
                with patch("models.refit_eta.joblib.load") as load:
                    with self.assertRaises(ValueError):
                        load_artifact(self.output)
                    load.assert_not_called()

    def test_corrupt_model_rejected_before_deserialization(self):
        self.refit()
        with (self.output / "model.joblib").open("ab") as file:
            file.write(b"corruption")
        with patch("models.refit_eta.joblib.load") as load:
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_artifact(self.output)
            load.assert_not_called()

    def test_roundtrip_mismatch_does_not_publish_metadata(self):
        wrong_model = SimpleNamespace(predict=lambda rows: [0.0] * len(rows))
        with patch("models.refit_eta.joblib.load", return_value=wrong_model):
            with self.assertRaisesRegex(AssertionError, "predictions differ"):
                self.refit()
        self.assertFalse((self.output / "metadata.json").exists())


if __name__ == "__main__":
    unittest.main()
