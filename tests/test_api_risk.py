"""API risk tests: late_probability served, stored, and retried identically.

Needs the local stack with migrations 001+002 applied. Same credential rules
as tests/test_api_logging.py: app credentials drive the API, admin credentials
are cleanup-only. From fish:

    env (grep -E '^POSTGRES_(DB|ADMIN_USER|ADMIN_PASSWORD|APP_USER|APP_PASSWORD)=' .env) \
        .venv/bin/python -W error -m unittest tests.test_api_risk -v

Without those variables the whole class skips and the database is untouched.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient

from models.refit_eta import refit_eta
from models.refit_risk import refit_risk
from persistence.predictions import insert_run
from serving.api import create_app
from simulator.generate_orders import generate_order

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/prediction-request.example.json"
RUN = "TEST-RISK"
MANIFEST = {
    "source_sha256": "risk-test",
    "source_order_count": 1,
    "scenario": {"test": True},
    "code_commit": "c0ffee",
    "image_id": "img-1",
    "model_sha256": "model-1",
    "model_metadata_sha256": "meta-1",
}
APP_KEYS = ("POSTGRES_DB", "POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")
ADMIN_KEYS = ("POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD")


def db_kwargs(user_key, password_key):
    return {"host": os.environ.get("PGHOST", "127.0.0.1"), "port": int(os.environ.get("PGPORT", "5432")),
            "dbname": os.environ["POSTGRES_DB"], "user": os.environ[user_key],
            "password": os.environ[password_key]}


def headers(run_id=RUN, predicted_at="2026-09-03T18:00:00-04:00"):
    return {"X-Run-Id": run_id, "X-Predicted-At": predicted_at}


class TestAPIRisk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [k for k in APP_KEYS + ADMIN_KEYS if k not in os.environ]
        if missing:
            raise unittest.SkipTest(f"DB credentials not set ({', '.join(missing)}); skipping")
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.artifact = cls.directory / "artifact"
        cls.risk_artifact = cls.directory / "risk-artifact"
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        orders = [generate_order(start + timedelta(days=2*i), order_id=f"APIRISK-{i}",
                                 cancellation_probability=0) for i in range(120)]
        data = cls.directory / "training.json"
        data.write_text(json.dumps(orders), encoding="utf-8")
        observed = datetime(2026, 9, 3, tzinfo=timezone.utc)
        refit_eta(data, cls.artifact, observed_at=observed)
        refit_risk(data, cls.risk_artifact, observed_at=observed)
        cls.risk_sha256 = json.loads((cls.risk_artifact / "metadata.json")
                                     .read_text(encoding="utf-8"))["model_sha256"]
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            insert_run(conn, run_id=RUN, **MANIFEST)
        cls.addClassCleanup(cls.delete_scratch_rows)

    @classmethod
    def delete_scratch_rows(cls):
        with psycopg.connect(**db_kwargs("POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD")) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM app.predictions WHERE run_id = %s;", (RUN,))
                cur.execute("DELETE FROM app.runs WHERE run_id = %s;", (RUN,))
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM app.predictions WHERE run_id = %s;", (RUN,))
                leftovers = cur.fetchone()[0]
        if leftovers:
            raise AssertionError(f"{leftovers} scratch risk rows were not cleaned up")

    def setUp(self):
        self.request = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def app(self):
        return create_app(self.artifact, self.risk_artifact)

    def stored_proba(self, order_id):
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT late_probability FROM app.predictions "
                            "WHERE run_id = %s AND order_id = %s;", (RUN, order_id))
                row = cur.fetchone()
                return row[0] if row else None

    def test_logged_risk_is_stored_and_returned(self):
        body = {**self.request, "order_id": "RISK-001"}
        with TestClient(self.app()) as client:
            response = client.post("/predict", json=body, headers=headers())
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertIn("late_probability", payload)
            self.assertIn("risk_model_sha256", payload)
            self.assertTrue(0.0 <= payload["late_probability"] <= 1.0)
            self.assertEqual(payload["risk_model_sha256"], self.risk_sha256)
            self.assertEqual(client.get("/health").json()["risk_model_sha256"], self.risk_sha256)
            self.assertEqual(self.stored_proba("RISK-001"), payload["late_probability"])

    def test_retry_returns_identical_risk(self):
        body = {**self.request, "order_id": "RISK-002"}
        with TestClient(self.app()) as client:
            first = client.post("/predict", json=body, headers=headers())
            second = client.post("/predict", json=body, headers=headers())
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.json(), first.json())

    def test_eta_only_shape_without_risk_artifact(self):
        body = {**self.request, "order_id": "RISK-003"}
        with TestClient(create_app(self.artifact)) as client:
            health = client.get("/health").json()
            self.assertNotIn("risk_model_sha256", health)
            response = client.post("/predict", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("late_probability", response.json())

    def test_unlogged_risk_stores_nothing(self):
        body = {**self.request, "order_id": "RISK-004"}
        with TestClient(self.app()) as client:
            response = client.post("/predict", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("late_probability", response.json())
            self.assertIsNone(self.stored_proba("RISK-004"))

    def test_corrupt_risk_artifact_fails_startup(self):
        import shutil
        broken = Path(self.temp.name) / "broken-risk"
        shutil.copytree(self.risk_artifact, broken)
        with (broken / "model.joblib").open("ab") as file:
            file.write(b"corruption")
        with self.assertRaises(ValueError):
            with TestClient(create_app(self.artifact, broken)):
                pass


if __name__ == "__main__":
    unittest.main()
