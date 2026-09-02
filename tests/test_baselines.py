import unittest

from models.baselines import (
    GlobalMeanBaseline,
    HeuristicBaseline,
    LinearRegressionBaseline,
    compute_metrics,
    evaluate_baselines,
)


class TestBaselines(unittest.TestCase):
    def setUp(self):
        self.sample_orders = [
            {
                "order_id": "O1",
                "delivery_duration_minutes": 30.0,
                "distance_km": 2.0,
                "item_count": 2,
                "traffic_index": 1.0,
                "restaurant_backlog": 1,
                "orders_waiting_for_courier": 0,
                "idle_couriers": 2,
                "precipitation_mm_per_hour": 0.0,
            },
            {
                "order_id": "O2",
                "delivery_duration_minutes": 50.0,
                "distance_km": 4.0,
                "item_count": 4,
                "traffic_index": 1.5,
                "restaurant_backlog": 3,
                "orders_waiting_for_courier": 2,
                "idle_couriers": 1,
                "precipitation_mm_per_hour": 3.0,
            },
        ]

    def test_global_mean_baseline(self):
        model = GlobalMeanBaseline()
        with self.assertRaises(ValueError):
            model.predict(self.sample_orders)
        with self.assertRaises(ValueError):
            model.fit([])

        model.fit(self.sample_orders)
        self.assertEqual(model.mean_duration, 40.0)
        preds = model.predict(self.sample_orders)
        self.assertEqual(preds, [40.0, 40.0])

    def test_heuristic_baseline(self):
        model = HeuristicBaseline(base_prep_min=15.0, per_item_min=2.0, min_per_km=4.0)
        preds = model.predict(self.sample_orders)
        # O1: prep = 15 + 2*2 = 19; travel = 4 * 2.0 * 1.0 = 8 -> 27.0
        # O2: prep = 15 + 2*4 = 23; travel = 4 * 4.0 * 1.5 = 24 -> 47.0
        self.assertEqual(preds, [27.0, 47.0])

        # Floor test
        low_order = [{
            "order_id": "O_LOW",
            "item_count": 0,
            "distance_km": 0.0,
            "traffic_index": 1.0,
        }]
        low_model = HeuristicBaseline(base_prep_min=1.0, per_item_min=0.0, min_per_km=0.0)
        self.assertEqual(low_model.predict(low_order), [5.0])

    def test_linear_regression_baseline(self):
        model = LinearRegressionBaseline()
        with self.assertRaises(ValueError):
            model.predict(self.sample_orders)
        with self.assertRaises(ValueError):
            model.fit([])

        model.fit(self.sample_orders)
        self.assertTrue(model.is_fitted)
        preds = model.predict(self.sample_orders)
        self.assertEqual(len(preds), 2)
        for p in preds:
            self.assertGreaterEqual(p, 5.0)

    def test_compute_metrics(self):
        with self.assertRaises(ValueError):
            compute_metrics([], [])
        with self.assertRaises(ValueError):
            compute_metrics([10.0], [10.0, 20.0])

        perfect = compute_metrics([20.0, 40.0], [20.0, 40.0])
        self.assertEqual(perfect["mae"], 0.0)
        self.assertEqual(perfect["mean_bias"], 0.0)
        self.assertEqual(perfect["rmse"], 0.0)
        self.assertEqual(perfect["p95_error"], 0.0)

        # Overestimate by 5 on first, underestimate by 3 on second
        # errors: [25 - 20 = +5, 37 - 40 = -3]
        # abs errors: [5, 3], MAE = 4.0, Bias = (5 - 3) / 2 = +1.0
        offset = compute_metrics([20.0, 40.0], [25.0, 37.0])
        self.assertEqual(offset["mae"], 4.0)
        self.assertEqual(offset["mean_bias"], 1.0)

    def test_evaluate_baselines_pipeline(self):
        splits = {
            "train": self.sample_orders,
            "val": self.sample_orders,
            "test": self.sample_orders,
        }
        results, lr_model = evaluate_baselines(splits)
        self.assertIn("Global Mean", results)
        self.assertIn("Domain Heuristic", results)
        self.assertIn("Linear Regression", results)
        for model_name in results:
            for split_name in ("train", "val", "test"):
                self.assertIn("mae", results[model_name][split_name])
                self.assertIn("mean_bias", results[model_name][split_name])
                self.assertIn("p95_error", results[model_name][split_name])
                self.assertIn("rmse", results[model_name][split_name])


if __name__ == "__main__":
    unittest.main()

