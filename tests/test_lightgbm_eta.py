import unittest

from models.lightgbm_eta import (
    ALL_FEATURES,
    CATEGORICAL_FEATURES,
    NUMERICAL_FEATURES,
    LightGBMETAModel,
    evaluate_eta_models,
    extract_features,
    extract_target,
)


class TestLightGBMETA(unittest.TestCase):
    def setUp(self):
        # Create a tiny synthetic sample with diverse values for testing
        self.sample_orders = [
            {
                "order_id": f"O{i:03d}",
                "delivery_duration_minutes": 25.0 + i * 2.0,
                "distance_km": 1.5 + (i % 3) * 0.5,
                "item_count": 1 + (i % 4),
                "traffic_index": 1.0 + (i % 2) * 0.5,
                "restaurant_backlog": i % 3,
                "orders_waiting_for_courier": i % 2,
                "idle_couriers": 3 - (i % 2),
                "precipitation_mm_per_hour": float(i % 5),
                "temperature_c": 15.0 - float(i % 10),
                "local_hour": 12 + (i % 6),
                "day_of_week": i % 7,
                "pickup_zone_id": ("Z1", "Z2", "Z3")[i % 3],
                "dropoff_zone_id": ("Z1", "Z2", "Z3")[(i + 1) % 3],
                "weather_type": ("clear", "rain", "snow", "storm")[i % 4],
            }
            for i in range(30)
        ]

    def test_feature_extraction(self):
        X = extract_features(self.sample_orders)
        y = extract_target(self.sample_orders)

        self.assertEqual(X.shape, (30, len(ALL_FEATURES)))
        self.assertEqual(y.shape, (30,))
        self.assertEqual(len(ALL_FEATURES), len(NUMERICAL_FEATURES) + len(CATEGORICAL_FEATURES))

        # Check unknown category handling defaults to -1
        unknown_cat_order = [dict(self.sample_orders[0], pickup_zone_id="UNKNOWN", weather_type="tornado")]
        X_unknown = extract_features(unknown_cat_order)
        pickup_idx = ALL_FEATURES.index("pickup_zone_id")
        weather_idx = ALL_FEATURES.index("weather_type")
        self.assertEqual(X_unknown[0, pickup_idx], -1.0)
        self.assertEqual(X_unknown[0, weather_idx], -1.0)

    def test_model_lifecycle_and_predictions(self):
        model = LightGBMETAModel(n_estimators=10, num_leaves=7, min_child_samples=2)

        # Unfitted call raises error
        with self.assertRaises(ValueError):
            model.predict(self.sample_orders)
        with self.assertRaises(ValueError):
            model.fit([])

        # Fit without validation set
        model.fit(self.sample_orders)
        self.assertTrue(model.is_fitted)

        preds = model.predict(self.sample_orders)
        self.assertEqual(len(preds), 30)
        for p in preds:
            self.assertGreaterEqual(p, 5.0)

        # Empty prediction list
        self.assertEqual(model.predict([]), [])

    def test_fit_with_validation_and_early_stopping(self):
        train = self.sample_orders[:20]
        val = self.sample_orders[20:]

        model = LightGBMETAModel(n_estimators=50, num_leaves=7, min_child_samples=2, early_stopping_rounds=5)
        model.fit(train, val_orders=val)
        self.assertTrue(model.is_fitted)
        self.assertGreater(model.model.best_iteration_, 0)
        self.assertLess(model.model.best_iteration_, model.n_estimators)

        # Feature importances
        importances = model.get_feature_importances("gain")
        self.assertEqual(set(importances.keys()), set(ALL_FEATURES))
        for feat, val in importances.items():
            self.assertGreaterEqual(val, 0.0)

    def test_evaluate_eta_models_pipeline(self):
        train = self.sample_orders[:15]
        val = self.sample_orders[15:22]
        test = self.sample_orders[22:]

        splits = {"train": train, "val": val, "test": test}
        results, lgb_model, lr_model = evaluate_eta_models(splits, include_test=True)

        for model_name in ("Global Mean", "Linear Regression", "LightGBM"):
            self.assertIn(model_name, results)
            for split_name in ("train", "val", "test"):
                metrics = results[model_name][split_name]
                for k in ("mae", "mean_bias", "p95_error", "rmse"):
                    self.assertIn(k, metrics)
                    self.assertIsInstance(metrics[k], float)

    def test_test_set_is_not_read_without_opt_in(self):
        results, _, _ = evaluate_eta_models({
            "train": self.sample_orders[:20], "val": self.sample_orders[20:], "test": None,
        })
        for metrics in results.values():
            self.assertEqual(set(metrics), {"train", "val"})

    def test_test_labels_do_not_change_fitted_model(self):
        splits = {"train": self.sample_orders[:20], "val": self.sample_orders[20:25], "test": self.sample_orders[25:]}
        _, original, _ = evaluate_eta_models(splits, include_test=True)
        splits["test"] = [dict(o, delivery_duration_minutes=999.0) for o in splits["test"]]
        _, changed, _ = evaluate_eta_models(splits, include_test=True)
        self.assertEqual(original.model.best_iteration_, changed.model.best_iteration_)
        self.assertEqual(original.predict(self.sample_orders), changed.predict(self.sample_orders))

    def test_predictions_do_not_require_outcomes(self):
        model = LightGBMETAModel(n_estimators=10, min_child_samples=2).fit(self.sample_orders)
        inputs = [{k: v for k, v in o.items() if k != "delivery_duration_minutes"} for o in self.sample_orders]
        self.assertEqual(model.predict(inputs), model.predict(self.sample_orders))


if __name__ == "__main__":
    unittest.main()
