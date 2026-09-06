import math
import unittest
from datetime import datetime, timedelta, timezone

from models.late_risk import (
    ConstantRiskBaseline,
    ETAThresholdBaseline,
    LightGBMRiskModel,
    calibration_deciles,
    compute_risk_metrics,
    evaluate_risk_models,
    promise_minutes,
    segment_log_loss,
)
from models.lightgbm_eta import LightGBMETAModel
from simulator.generate_orders import generate_order


def delivered_orders(n, seed=42):
    start = datetime(2026, 3, 2, tzinfo=timezone.utc)
    return [generate_order(start + timedelta(hours=3 * i), order_id=f"RISK-{seed}-{i}",
                           seed=seed, cancellation_probability=0) for i in range(n)]


class TestLateRisk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.train = delivered_orders(160)
        cls.val = delivered_orders(60, seed=7)
        cls.test = delivered_orders(60, seed=11)
        for name, split in (("train", cls.train), ("val", cls.val), ("test", cls.test)):
            rates = sum(1 for o in split if o["late_delivery"]) / len(split)
            assert 0 < rates < 1, f"{name} lacks both classes; regenerate with another seed"

    def splits(self):
        return {"train": self.train, "val": self.val, "test": self.test}

    def test_promise_uses_order_timestamps(self):
        self.assertEqual(promise_minutes(self.train[0]), 45.0)

    def test_constant_baseline_predicts_train_rate(self):
        model = ConstantRiskBaseline().fit(self.train)
        expected = sum(1 for o in self.train if o["late_delivery"]) / len(self.train)
        self.assertEqual(model.predict(self.val), [expected] * len(self.val))

    def test_threshold_baseline_stays_within_epsilon_bounds(self):
        eta = LightGBMETAModel().fit(self.train, val_orders=self.val)
        preds = ETAThresholdBaseline(eta).predict(self.val)
        self.assertTrue(all(0.05 <= p <= 0.95 for p in preds))
        self.assertGreater(max(preds), min(preds))

    def test_unfitted_models_raise_and_empty_fit_fails(self):
        with self.assertRaises(ValueError):
            ConstantRiskBaseline().predict(self.val)
        with self.assertRaises(ValueError):
            ConstantRiskBaseline().fit([])
        with self.assertRaises(ValueError):
            LightGBMRiskModel().predict(self.val)
        with self.assertRaises(ValueError):
            LightGBMRiskModel().fit([])

    def test_classifier_probabilities_are_deterministic(self):
        first = LightGBMRiskModel().fit(self.train, val_orders=self.val).predict(self.val)
        second = LightGBMRiskModel().fit(self.train, val_orders=self.val).predict(self.val)
        self.assertEqual(first, second)
        self.assertTrue(all(0.0 <= p <= 1.0 for p in first))
        self.assertEqual(LightGBMRiskModel().fit(self.train).predict([]), [])

    def test_risk_metrics_match_hand_computation(self):
        metrics = compute_risk_metrics([0.0, 1.0], [0.25, 0.75])
        self.assertEqual(metrics["log_loss"], round(-math.log(0.75), 4))
        self.assertEqual(metrics["brier"], 0.0625)
        self.assertEqual(metrics["auc"], 1.0)
        with self.assertRaises(ValueError):
            compute_risk_metrics([0.0], [])
        with self.assertRaises(ValueError):
            compute_risk_metrics([], [])
        with self.assertRaises(ValueError):
            compute_risk_metrics([0.0, 1.0], [0.25, 1.5])

    def test_calibration_counts_reconcile(self):
        y_true = [float(o["late_delivery"]) for o in self.val]
        model = LightGBMRiskModel().fit(self.train, val_orders=self.val)
        table = calibration_deciles(y_true, model.predict(self.val), bins=5)
        self.assertEqual(sum(row["count"] for row in table), len(self.val))
        self.assertTrue(all(0.0 <= row["mean_predicted"] <= 1.0 for row in table))

    def test_segments_cover_only_observed_weather(self):
        model = ConstantRiskBaseline().fit(self.train)
        segments = segment_log_loss(self.val, model.predict(self.val))
        self.assertEqual(set(segments), {o["weather_type"] for o in self.val})
        self.assertTrue(all(s["count"] > 0 for s in segments.values()))

    def test_test_scoring_is_opt_in_and_fit_ignores_test_labels(self):
        default, _ = evaluate_risk_models(self.splits())
        self.assertEqual(set(default["LightGBM Risk"]), {"train", "val"})
        opted, _ = evaluate_risk_models(self.splits(), include_test=True)
        self.assertEqual(set(opted["LightGBM Risk"]), {"train", "val", "test"})
        for order in self.test:
            order["late_delivery"] = not order["late_delivery"]
        refit, _ = evaluate_risk_models(self.splits())
        self.assertEqual(refit["LightGBM Risk"]["val"], default["LightGBM Risk"]["val"])


if __name__ == "__main__":
    unittest.main()
