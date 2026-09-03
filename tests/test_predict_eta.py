import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from models.lightgbm_eta import ALL_FEATURES, NUMERICAL_FEATURES
from models.predict_eta import COUNT_MINIMUMS, DERIVED_FEATURES, REQUEST_FIELDS, predict_eta, validate_request
from models.refit_eta import load_artifact, refit_eta
from simulator.generate_orders import generate_order

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/prediction-request.example.json"


class TestPredictionRequest(unittest.TestCase):
    def setUp(self):
        self.request = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def test_valid_request_has_exact_model_features_and_is_not_mutated(self):
        original = copy.deepcopy(self.request)
        features = validate_request(self.request)
        self.assertEqual(set(features), set(ALL_FEATURES))
        self.assertEqual(features["local_hour"], 18)
        self.assertEqual(features["day_of_week"], 3)
        self.assertEqual(self.request, original)
        for field in set(ALL_FEATURES) - set(DERIVED_FEATURES):
            self.assertEqual(features[field], self.request[field])

    def test_calendar_uses_toronto_midnight_and_dst(self):
        cases = (
            ("2026-01-01T02:00:00Z", 21, 2),  # Previous local date/year.
            ("2026-03-08T06:59:00Z", 1, 6),
            ("2026-03-08T07:00:00Z", 3, 6),  # Spring-forward.
            ("2026-11-01T05:30:00Z", 1, 6),
            ("2026-11-01T06:30:00Z", 1, 6),  # Both fall-back hours.
        )
        for timestamp, hour, weekday in cases:
            with self.subTest(timestamp=timestamp):
                result = validate_request(dict(self.request, confirmed_at=timestamp))
                self.assertEqual((result["local_hour"], result["day_of_week"]), (hour, weekday))
        utc = dict(self.request, confirmed_at="2026-09-03T22:00:00Z")
        self.assertEqual(validate_request(utc), validate_request(self.request))

    def test_missing_fields(self):
        for field in REQUEST_FIELDS:
            request = dict(self.request)
            del request[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, f"Missing fields: {field}"):
                validate_request(request)

    def test_extra_fields_including_outcomes_are_rejected(self):
        for field in ("status", "delivered_at", "delivery_duration_minutes", "late_delivery",
                      "local_hour", "day_of_week", "promised_delivery_at", "typo"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, f"Unexpected fields: {field}"):
                validate_request(dict(self.request, **{field: None}))

    def test_request_shape_id_and_timestamp(self):
        for request in (None, [], [self.request], "order", {1: "value"}):
            with self.subTest(request=request), self.assertRaisesRegex(ValueError, "JSON object"):
                validate_request(request)
        for order_id in (None, "", "  ", 123, True):
            with self.subTest(order_id=order_id), self.assertRaisesRegex(ValueError, "order_id"):
                validate_request(dict(self.request, order_id=order_id))
        for timestamp in (None, 123, "bad", "2026-09-03", "2026-09-03T18:00:00",
                          "0001-01-01T00:00:00Z", "9999-12-31T23:59:59-12:00"):
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(ValueError, "confirmed_at"):
                validate_request(dict(self.request, confirmed_at=timestamp))

    def test_numeric_types_and_nonfinite_values(self):
        for field in set(NUMERICAL_FEATURES) - set(DERIVED_FEATURES):
            for value in (None, True, False, "2", [], {}, float("nan"), float("inf"), -float("inf"), 10**400):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                    validate_request(dict(self.request, **{field: value}))

    def test_counts_require_integers_and_minimums(self):
        for field, minimum in COUNT_MINIMUMS.items():
            for value in (minimum - 1, 1.5, 2.0):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                    validate_request(dict(self.request, **{field: value}))
            validate_request(dict(self.request, **{field: minimum}))

    def test_physical_ranges(self):
        for field in ("distance_km", "traffic_index"):
            for value in (0, -1):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                    validate_request(dict(self.request, **{field: value}))
        with self.assertRaisesRegex(ValueError, "precipitation_mm_per_hour"):
            validate_request(dict(self.request, precipitation_mm_per_hour=-1))

    def test_known_categories_and_unknown_categories(self):
        for field in ("pickup_zone_id", "dropoff_zone_id", "weather_type"):
            for value in ("unknown", "", None, 1, True, [], {}):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                    validate_request(dict(self.request, **{field: value}))
        for zone in ("Z1", "Z2", "Z3"):
            validate_request(dict(self.request, pickup_zone_id=zone, dropoff_zone_id=zone))
        for weather, temperature, rain in (("clear", -5, 0), ("rain", 1, 3), ("snow", 0, 2), ("storm", 1, 10)):
            validate_request(dict(self.request, weather_type=weather, temperature_c=temperature,
                                  precipitation_mm_per_hour=rain))

    def test_weather_consistency_matches_simulator(self):
        cases = (("clear", 18, 1), ("rain", 18, 0), ("snow", -1, 0), ("storm", 18, 0),
                 ("snow", 1, 2), ("rain", 0, 3), ("storm", -1, 10))
        for weather, temperature, rain in cases:
            with self.subTest(weather=weather, temperature=temperature, rain=rain), self.assertRaises(ValueError):
                validate_request(dict(self.request, weather_type=weather, temperature_c=temperature,
                                      precipitation_mm_per_hour=rain))

    def test_prediction_uses_loader_and_returns_identity_and_duration(self):
        model = Mock()
        model.predict.return_value = [42.25]
        metadata = {"model_sha256": "a" * 64, "simulated": True}
        with patch("models.predict_eta.load_artifact", return_value=(model, metadata)) as load:
            result = predict_eta(self.request, "chosen-artifact")
        load.assert_called_once_with("chosen-artifact")
        model.predict.assert_called_once_with([validate_request(self.request)])
        self.assertEqual(result, {"order_id": "EXAMPLE-001", "predicted_delivery_duration_minutes": 42.25,
                                  "model_sha256": "a" * 64, "simulated": True})
        model.fit.assert_not_called()

    def test_invalid_request_is_rejected_before_loading_model(self):
        with patch("models.predict_eta.load_artifact") as load:
            with self.assertRaises(ValueError):
                predict_eta(dict(self.request, status="delivered"), "unused")
            load.assert_not_called()


class TestPredictionCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.artifact = cls.directory / "artifact"
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        cls.orders = [generate_order(start + timedelta(days=2*i), order_id=f"CLI-{i}",
                                     cancellation_probability=0) for i in range(120)]
        data = cls.directory / "training.json"
        data.write_text(json.dumps(cls.orders), encoding="utf-8")
        refit_eta(data, cls.artifact, observed_at=datetime(2026, 9, 3, tzinfo=timezone.utc))

    def run_cli(self, input_path):
        return subprocess.run([sys.executable, "-W", "error", "-m", "models.predict_eta",
                               "--model-dir", str(self.artifact), "--input", str(input_path)],
                              capture_output=True, text=True, check=False)

    def test_cli_and_python_match_saved_model_without_retraining(self):
        request = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        model, metadata = load_artifact(self.artifact)
        with patch("models.lightgbm_eta.LightGBMETAModel.fit", side_effect=AssertionError("Must not train")):
            expected = predict_eta(request, self.artifact)
        self.assertEqual(expected["predicted_delivery_duration_minutes"],
                         model.predict([validate_request(request)])[0])
        self.assertEqual(expected["model_sha256"], metadata["model_sha256"])
        result = self.run_cli(EXAMPLE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def test_generated_requests_match_original_feature_rows(self):
        model, _ = load_artifact(self.artifact)
        requests = [{field: order[field] for field in REQUEST_FIELDS} for order in self.orders]
        features = [validate_request(request) for request in requests]
        for original, row in zip(self.orders, features):
            self.assertEqual(row, {field: original[field] for field in ALL_FEATURES})
        self.assertEqual(model.predict(features), model.predict(self.orders))

    def test_cli_reports_invalid_json_input_and_missing_files_without_predictions(self):
        invalid = self.directory / "invalid.json"
        for contents in ("{", "[]", '{"order_id": "ONLY-ID"}'):
            invalid.write_text(contents, encoding="utf-8")
            result = self.run_cli(invalid)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertTrue(result.stderr.startswith("error: "), result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        result = self.run_cli(self.directory / "missing.json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
