"""API logging tests: commit-before-response through real HTTP + real Postgres.

Needs the local stack with migration 001 applied. App credentials drive the
API; admin credentials are used only by class cleanup to delete scratch rows
(eta_app holds no DELETE grant), keeping zero residue. From fish:

    env (grep -E '^POSTGRES_(DB|ADMIN_USER|ADMIN_PASSWORD|APP_USER|APP_PASSWORD)=' .env) \
        .venv/bin/python -W error -m unittest tests.test_api_logging -v

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
RUN = "TEST-API-LOG"
MANIFEST = {
    "source_sha256": "api-test",
    "source_order_count": 1,
    "scenario": {"test": True},
    "code_commit": "c0ffee",
    "image_id": "img-1",
    "model_sha256": "model-1",
    "model_metadata_sha256": "meta-1",
}
APP_KEYS = ("POSTGRES_DB", "POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")
ADMIN_KEYS = ("POSTGRES_DB", "POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD")


def app_kwargs():
    return {"host": os.environ.get("PGHOST", "127.0.0.1"), "port": int(os.environ.get("PGPORT", "5432")),
            "dbname": os.environ["POSTGRES_DB"], "user": os.environ["POSTGRES_APP_USER"],
            "password": os.environ["POSTGRES_APP_PASSWORD"]}


def admin_kwargs():
    return {**app_kwargs(), "user": os.environ["POSTGRES_ADMIN_USER"],
            "password": os.environ["POSTGRES_ADMIN_PASSWORD"]}


def headers(run_id=RUN, predicted_at="2026-09-03T18:00:00-04:00"):
    return {"X-Run-Id": run_id, "X-Predicted-At": predicted_at}


class TestAPILogging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [k for k in APP_KEYS + ("POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD")
                   if k not in os.environ]
        if missing:
            raise unittest.SkipTest(f"DB credentials not set ({', '.join(missing)}); skipping")
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.artifact = cls.directory / "artifact"
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        orders = [generate_order(start + timedelta(days=2*i), order_id=f"APILOG-{i}",
                                 cancellation_probability=0) for i in range(120)]
        data = cls.directory / "training.json"
        data.write_text(json.dumps(orders), encoding="utf-8")
        refit_eta(data, cls.artifact, observed_at=datetime(2026, 9, 3, tzinfo=timezone.utc))
        with psycopg.connect(**app_kwargs()) as conn:
            insert_run(conn, run_id=RUN, **MANIFEST)
        cls.addClassCleanup(cls.delete_scratch_rows)

    @classmethod
    def delete_scratch_rows(cls):
        # LOG-% order IDs belong to this file alone; the headerless sub-case
        # leaves NULL-run attempts that no run-scoped delete can see.
        with psycopg.connect(**admin_kwargs()) as conn:
            with conn.cursor() as cur:
                for run_id in (RUN, "TEST-NOPE"):
                    cur.execute("DELETE FROM app.attempts WHERE run_id = %s;", (run_id,))
                    cur.execute("DELETE FROM app.predictions WHERE run_id = %s;", (run_id,))
                cur.execute("DELETE FROM app.runs WHERE run_id = %s;", (RUN,))
                cur.execute("DELETE FROM app.attempts WHERE run_id IS NULL AND order_id LIKE 'LOG-%%';")
        with psycopg.connect(**app_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT (SELECT count(*) FROM app.predictions WHERE run_id = ANY(%s)) + "
                            "(SELECT count(*) FROM app.attempts WHERE run_id = ANY(%s)) + "
                            "(SELECT count(*) FROM app.attempts WHERE run_id IS NULL "
                            "AND order_id LIKE 'LOG-%%');",
                            ([RUN, "TEST-NOPE"], [RUN, "TEST-NOPE"]))
                leftovers = cur.fetchone()[0]
        if leftovers:
            raise AssertionError(f"{leftovers} scratch prediction rows were not cleaned up")

    def setUp(self):
        self.request = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.app = create_app(self.artifact)

    def post(self, client, order_id, run_headers=True, **header_overrides):
        body = {**self.request, "order_id": order_id}
        header_values = headers() if run_headers else {}
        header_values.update(header_overrides)
        return client.post("/predict", json=body, headers=header_values), body

    def stored_count(self, order_id):
        with psycopg.connect(**app_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM app.predictions WHERE run_id = %s AND order_id = %s;",
                            (RUN, order_id))
                return cur.fetchone()[0]

    def test_logged_prediction_is_committed_before_responding(self):
        with TestClient(self.app) as client:
            response, _ = self.post(client, "LOG-001")
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["order_id"], "LOG-001")
            self.assertEqual(body["model_sha256"], client.get("/health").json()["model_sha256"])
            self.assertEqual(self.stored_count("LOG-001"), 1)

    def test_unlogged_prediction_stores_nothing(self):
        with TestClient(self.app) as client:
            response, _ = self.post(client, "LOG-002", run_headers=False)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.stored_count("LOG-002"), 0)

    def test_identical_retry_returns_identical_response_and_one_row(self):
        with TestClient(self.app) as client:
            first, _ = self.post(client, "LOG-003")
            second, _ = self.post(client, "LOG-003")
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.json(), first.json())
            self.assertEqual(self.stored_count("LOG-003"), 1)

    def test_conflicting_retry_is_409_and_keeps_original(self):
        with TestClient(self.app) as client:
            first, _ = self.post(client, "LOG-004")
            self.assertEqual(first.status_code, 200)
            body = {**self.request, "order_id": "LOG-004", "distance_km": 4.9}
            conflict = client.post("/predict", json=body, headers=headers())
            self.assertEqual(conflict.status_code, 409, conflict.text)
            self.assertEqual(self.stored_count("LOG-004"), 1)

    def test_half_logging_context_is_422(self):
        with TestClient(self.app) as client:
            only_run = client.post("/predict", json={**self.request, "order_id": "LOG-005"},
                                   headers={"X-Run-Id": RUN})
            self.assertEqual(only_run.status_code, 422)
            only_time = client.post("/predict", json={**self.request, "order_id": "LOG-005"},
                                    headers={"X-Predicted-At": "2026-09-03T18:00:00-04:00"})
            self.assertEqual(only_time.status_code, 422)
            naive = client.post("/predict", json={**self.request, "order_id": "LOG-005"},
                                headers=headers(predicted_at="2026-09-03T18:00:00"))
            self.assertEqual(naive.status_code, 422)
            self.assertEqual(self.stored_count("LOG-005"), 0)

    def test_unknown_run_is_422_and_stores_nothing(self):
        with TestClient(self.app) as client:
            response, _ = self.post(client, "LOG-006")
            self.assertEqual(response.status_code, 200)  # Sanity: known run works.
            unknown, _ = self.post(client, "LOG-007", **{"X-Run-Id": "TEST-NOPE"})
            self.assertEqual(unknown.status_code, 422, unknown.text)
            with psycopg.connect(**app_kwargs()) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM app.predictions WHERE run_id = %s;",
                                ("TEST-NOPE",))
                    self.assertEqual(cur.fetchone()[0], 0)

    def test_run_headers_without_database_is_503(self):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("POSTGRES_")}
        with patch.dict(os.environ, clean, clear=True):
            with TestClient(self.app) as client:
                response, _ = self.post(client, "LOG-008")
                self.assertEqual(response.status_code, 503, response.text)
                plain, _ = self.post(client, "LOG-008", run_headers=False)
                self.assertEqual(plain.status_code, 200)

    def test_unreachable_database_fails_startup(self):
        with patch.dict(os.environ, {**os.environ, "PGPORT": "5499"}):
            with self.assertRaises(RuntimeError):
                with TestClient(self.app):
                    pass

    def test_database_error_mid_request_is_503_with_no_row(self):
        with TestClient(self.app) as client:
            with patch("serving.api.insert_prediction",
                       side_effect=psycopg.OperationalError("boom")):
                response, _ = self.post(client, "LOG-009")
                self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(self.stored_count("LOG-009"), 0)


if __name__ == "__main__":
    unittest.main()
