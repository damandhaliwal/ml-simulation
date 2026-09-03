import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from models.predict_eta import REQUEST_FIELDS, predict_eta
from models.refit_eta import file_sha256, load_artifact, refit_eta
from serving.api import create_app, main
from simulator.generate_orders import generate_order

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/prediction-request.example.json"


class TestETAAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.artifact = cls.directory / "artifact"
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        cls.orders = [generate_order(start + timedelta(days=2*i), order_id=f"API-{i}",
                                     cancellation_probability=0) for i in range(120)]
        data = cls.directory / "training.json"
        data.write_text(json.dumps(cls.orders), encoding="utf-8")
        cls.metadata = refit_eta(data, cls.artifact, observed_at=datetime(2026, 9, 3, tzinfo=timezone.utc))

    def setUp(self):
        self.request = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def test_creation_does_not_load_and_unstarted_app_is_not_ready(self):
        with patch("serving.api.load_artifact") as load:
            app = create_app(self.artifact)
            client = TestClient(app)  # No context manager means no startup lifecycle.
            self.addCleanup(client.close)
            self.assertEqual(client.get("/health").status_code, 503)
            self.assertEqual(client.post("/predict", json=self.request).status_code, 503)
            load.assert_not_called()

    def test_model_loaded_once_and_api_matches_python_interface(self):
        expected = predict_eta(self.request, self.artifact)
        app = create_app(self.artifact)
        with patch("serving.api.load_artifact", wraps=load_artifact) as load:
            with patch("models.lightgbm_eta.LightGBMETAModel.fit", side_effect=AssertionError("Must not train")):
                with TestClient(app) as client:
                    health = client.get("/health")
                    self.assertEqual(health.status_code, 200)
                    self.assertEqual(health.json(), {"status": "ready", "model_sha256": expected["model_sha256"],
                                                      "simulated": True})
                    for _ in range(3):
                        response = client.post("/predict", json=self.request)
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(response.json(), expected)
                    load.assert_called_once_with(self.artifact)
            self.assertIsNone(app.state.artifact)
        self.assertEqual(file_sha256(self.artifact / "model.joblib"), self.metadata["model_sha256"])

    def test_generated_orders_keep_prediction_parity_over_http(self):
        model, _ = load_artifact(self.artifact)
        with TestClient(create_app(self.artifact)) as client:
            predictions = []
            for order in self.orders:
                request = {field: order[field] for field in REQUEST_FIELDS}
                response = client.post("/predict", json=request)
                self.assertEqual(response.status_code, 200, response.text)
                predictions.append(response.json()["predicted_delivery_duration_minutes"])
            self.assertEqual(predictions, model.predict(self.orders))

    def test_domain_errors_return_422_without_prediction(self):
        invalid = [dict(self.request, **change) for change in (
            {"status": "delivered"}, {"delivery_duration_minutes": 42}, {"local_hour": 18},
            {"distance_km": -1}, {"item_count": 2.0}, {"item_count": True},
            {"traffic_index": "1.5"}, {"temperature_c": None}, {"idle_couriers": -1},
            {"pickup_zone_id": "Z4"}, {"weather_type": "unknown"},
            {"confirmed_at": "2026-09-03T18:00:00"}, {"precipitation_mm_per_hour": 0},
        )]
        invalid.append({field: value for field, value in self.request.items() if field != "item_count"})
        with TestClient(create_app(self.artifact)) as client:
            with patch.object(client.app.state.artifact[0], "predict") as predict:
                for request in invalid:
                    with self.subTest(request=request):
                        response = client.post("/predict", json=request)
                        self.assertEqual(response.status_code, 422, response.text)
                        self.assertIsInstance(response.json()["detail"], str)
                predict.assert_not_called()

    def test_bad_json_and_non_object_bodies_return_422(self):
        with TestClient(create_app(self.artifact)) as client:
            for body in ("", "{", "null", "[]", "true", "123", '"text"', "[NaN]", "NaN"):
                with self.subTest(body=body):
                    response = client.post("/predict", content=body, headers={"Content-Type": "application/json"})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json(), {"detail": "Body must be a valid JSON object"})

    def test_nonfinite_numbers_are_rejected_without_serialization_errors(self):
        with TestClient(create_app(self.artifact)) as client:
            for value in (float("nan"), float("inf"), -float("inf"), 10**400):
                body = json.dumps(dict(self.request, distance_km=value))
                response = client.post("/predict", content=body, headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("distance_km", response.json()["detail"])

    def test_model_errors_are_not_mislabeled_as_client_errors(self):
        with TestClient(create_app(self.artifact), raise_server_exceptions=False) as client:
            with patch.object(client.app.state.artifact[0], "predict", side_effect=ValueError("internal model failure")):
                response = client.post("/predict", json=self.request)
                self.assertEqual(response.status_code, 500)
                self.assertNotIn("internal model failure", response.text)

    def test_missing_artifact_aborts_startup(self):
        app = create_app(self.directory / "missing")
        with self.assertRaises(FileNotFoundError), TestClient(app):
            self.fail("A missing artifact must prevent startup")
        self.assertIsNone(app.state.artifact)

    def test_corrupt_artifact_aborts_startup(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "artifact"
            shutil.copytree(self.artifact, artifact)
            with (artifact / "model.joblib").open("ab") as file:
                file.write(b"corruption")
            with self.assertRaisesRegex(ValueError, "checksum"), TestClient(create_app(artifact)):
                self.fail("A corrupt artifact must prevent startup")

    def test_incompatible_metadata_aborts_startup(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "artifact"
            shutil.copytree(self.artifact, artifact)
            for field in ("runtime_versions", "feature_contract"):
                metadata = dict(self.metadata, **{field: {}})
                (artifact / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
                with self.subTest(field=field), self.assertRaises(ValueError), TestClient(create_app(artifact)):
                    self.fail("Incompatible metadata must prevent startup")

    def test_only_supported_methods_and_paths(self):
        with TestClient(create_app(self.artifact)) as client:
            self.assertEqual(client.get("/predict").status_code, 405)
            self.assertEqual(client.post("/health").status_code, 405)
            self.assertEqual(client.get("/missing").status_code, 404)

    def test_cli_binds_only_localhost_and_one_worker(self):
        with patch("sys.argv", ["api", "--model-dir", str(self.artifact), "--port", "8123"]):
            with patch("serving.api.uvicorn.run") as run:
                main()
        self.assertEqual(run.call_args.kwargs, {"host": "127.0.0.1", "port": 8123, "workers": 1})
        self.assertIsNone(run.call_args.args[0].state.artifact)


if __name__ == "__main__":
    unittest.main()
