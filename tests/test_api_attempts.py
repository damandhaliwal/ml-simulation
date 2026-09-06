"""Attempt-log tests: every non-200 outcome leaves an operational row, never a prediction.

Needs the local stack with migrations 001-003 applied. Same credential rules
as the other API DB tests: app credentials drive the API, admin credentials
are cleanup-only. From fish:

    env (grep -E '^POSTGRES_(DB|ADMIN_USER|ADMIN_PASSWORD|APP_USER|APP_PASSWORD)=' .env) \
        .venv/bin/python -W error -m unittest tests.test_api_attempts -v

Without those variables the whole class skips and the database is untouched.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

from models.refit_eta import refit_eta
from persistence.predictions import insert_run
from serving.api import create_app
from simulator.generate_orders import generate_order

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/prediction-request.example.json"
RUN = "TEST-ATTEMPTS"
MANIFEST = {
    "source_sha256": "attempts-test",
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


class TestAPIAttempts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [k for k in APP_KEYS + ADMIN_KEYS if k not in os.environ]
        if missing:
            raise unittest.SkipTest(f"DB credentials not set ({', '.join(missing)}); skipping")
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.artifact = cls.directory / "artifact"
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        orders = [generate_order(start + timedelta(days=2*i), order_id=f"APIATT-{i}",
                                 cancellation_probability=0) for i in range(120)]
        data = cls.directory / "training.json"
        data.write_text(json.dumps(orders), encoding="utf-8")
        refit_eta(data, cls.artifact, observed_at=datetime(2026, 9, 3, tzinfo=timezone.utc))
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            insert_run(conn, run_id=RUN, **MANIFEST)
        cls.addClassCleanup(cls.delete_scratch_rows)

    @classmethod
    def delete_scratch_rows(cls):
        with psycopg.connect(**db_kwargs("POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD")) as conn:
            with conn.cursor() as cur:
                for table in ("attempts", "predictions", "runs"):
                    cur.execute(f"DELETE FROM app.{table} WHERE run_id = %s;", (RUN,))
                cur.execute("DELETE FROM app.attempts WHERE run_id IS NULL "
                            "AND detail = 'Body must be a valid JSON object';")
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT (SELECT count(*) FROM app.predictions WHERE run_id = %s) + "
                            "(SELECT count(*) FROM app.attempts WHERE run_id = %s);", (RUN, RUN))
                leftovers = cur.fetchone()[0]
        if leftovers:
            raise AssertionError(f"{leftovers} scratch attempt rows were not cleaned up")

    def setUp(self):
        self.request = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def attempts_for(self, order_id):
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT http_status, category, detail, run_id, order_id, "
                            "attempt_latency_ms FROM app.attempts "
                            "WHERE order_id = %s ORDER BY recorded_at_wall;", (order_id,))
                return cur.fetchall()

    def prediction_count(self, order_id):
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM app.predictions WHERE run_id = %s AND order_id = %s;",
                            (RUN, order_id))
                return cur.fetchone()[0]

    def test_invalid_request_is_logged_without_prediction_or_body(self):
        body = {**self.request, "order_id": "ATT-001", "distance_km": "CANARY"}
        with TestClient(create_app(self.artifact)) as client:
            response = client.post("/predict", json=body, headers=headers())
            self.assertEqual(response.status_code, 422, response.text)
            rows = self.attempts_for("ATT-001")
            self.assertEqual(len(rows), 1)
            status, category, detail, run_id, order_id, latency = rows[0]
            self.assertEqual((status, category, run_id, order_id), (422, "invalid_request", RUN, "ATT-001"))
            self.assertNotIn("CANARY", detail)
            self.assertGreaterEqual(latency, 0.0)
            self.assertEqual(self.prediction_count("ATT-001"), 0)

    def test_malformed_json_is_logged_without_correlation(self):
        with TestClient(create_app(self.artifact)) as client:
            response = client.post("/predict", content=b"{",
                                   headers={"Content-Type": "application/json", **headers()})
            self.assertEqual(response.status_code, 422, response.text)
            with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT order_id, detail FROM app.attempts WHERE run_id = %s "
                                "AND detail = 'Body must be a valid JSON object';", (RUN,))
                    rows = cur.fetchall()
            self.assertEqual(rows, [(None, "Body must be a valid JSON object")])

    def test_conflict_is_logged_and_keeps_original(self):
        body = {**self.request, "order_id": "ATT-003"}
        with TestClient(create_app(self.artifact)) as client:
            self.assertEqual(client.post("/predict", json=body, headers=headers()).status_code, 200)
            conflict = client.post("/predict",
                                   json={**body, "distance_km": 4.9}, headers=headers())
            self.assertEqual(conflict.status_code, 409, conflict.text)
            rows = self.attempts_for("ATT-003")
            self.assertEqual([(r[0], r[1]) for r in rows], [(409, "conflict")])
            self.assertEqual(self.prediction_count("ATT-003"), 1)

    def test_store_failure_is_logged_without_prediction(self):
        body = {**self.request, "order_id": "ATT-004"}
        with TestClient(create_app(self.artifact)) as client:
            with patch("serving.api.insert_prediction",
                       side_effect=psycopg.OperationalError("boom")):
                response = client.post("/predict", json=body, headers=headers())
                self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(self.prediction_count("ATT-004"), 0)
            rows = self.attempts_for("ATT-004")
            self.assertEqual([(r[0], r[1]) for r in rows], [(503, "store_unavailable")])

    def test_broken_attempt_log_never_masks_the_response(self):
        body = {**self.request, "order_id": "ATT-005", "distance_km": "CANARY"}
        with TestClient(create_app(self.artifact)) as client:
            with patch("serving.api.psycopg.connect",
                       side_effect=psycopg.OperationalError("boom")):
                response = client.post("/predict", json=body, headers=headers())
                self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
