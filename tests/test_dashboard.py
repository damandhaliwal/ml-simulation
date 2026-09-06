"""Dashboard tests: pure rendering plus the live route against real Postgres.

DB route tests need the local stack; app credentials drive the API, admin
credentials are cleanup-only. From fish:

    env (grep -E '^POSTGRES_(DB|ADMIN_USER|ADMIN_PASSWORD|APP_USER|APP_PASSWORD)=' .env) \
        .venv/bin/python -W error -m unittest tests.test_dashboard -v

Without those variables only the pure rendering tests run.
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
from persistence.outcomes import insert_outcome
from persistence.predictions import insert_prediction, insert_run
from serving.api import create_app
from serving.dashboard import FAILED_BASELINE_NOTE, render_dashboard, run_panel
from simulator.generate_orders import generate_order

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/prediction-request.example.json"
RUN = "TEST-DASHBOARD"
FEATURES = {"distance_km": 2.5, "item_count": 2, "traffic_index": 1.5,
            "restaurant_backlog": 3, "orders_waiting_for_courier": 2,
            "idle_couriers": 1, "precipitation_mm_per_hour": 3,
            "temperature_c": 18.0, "pickup_zone_id": "Z1",
            "dropoff_zone_id": "Z2", "weather_type": "rain"}
APP_KEYS = ("POSTGRES_DB", "POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")
ADMIN_KEYS = ("POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD")


def db_kwargs(user_key, password_key):
    return {"host": os.environ.get("PGHOST", "127.0.0.1"), "port": int(os.environ.get("PGPORT", "5432")),
            "dbname": os.environ["POSTGRES_DB"], "user": os.environ[user_key],
            "password": os.environ[password_key]}


def panel(run_id=RUN, alert=False):
    return {"run_id": run_id,
            "metrics": {"matched_deliveries": 6, "mae_minutes": 3.32,
                        "bias_minutes": -0.568, "p95_minutes": 9.12,
                        "storm_bias_minutes": -4.646, "alert": alert},
            "findings": [{"check": "storm_bias"}] if alert else []}


class TestRenderDashboard(unittest.TestCase):
    def test_cards_findings_and_empty_state(self):
        html = render_dashboard([panel(), panel("OTHER", alert=False)])
        self.assertIn("TEST-DASHBOARD", html)
        self.assertIn("3.32", html)
        self.assertIn("storm_bias", html)
        self.assertIn("<li>none</li>", html)
        self.assertIn("simulated", html)
        self.assertIn(FAILED_BASELINE_NOTE, render_dashboard([], baseline_missing=True))
        self.assertIn("no logged runs", render_dashboard([]))

    def test_run_ids_are_escaped(self):
        html = render_dashboard([panel(run_id="<script>alert(1)</script>")])
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_panel_alert_mapping(self):
        performance = {"matched": 6,
                       "overall": {"mae": 3.32, "mean_bias": -0.568, "p95_error": 9.12, "rmse": 4.3},
                       "storm": {"mae": 5.4, "mean_bias": -4.6, "p95_error": 17.0, "rmse": 7.5}}
        quiet = run_panel(RUN, performance, [], [])
        self.assertFalse(quiet["metrics"]["alert"])
        self.assertEqual(quiet["findings"], [])
        loud = run_panel(RUN, performance, [{"feature": "weather_type"}], [{"check": "storm_bias"}])
        self.assertTrue(loud["metrics"]["alert"])
        self.assertEqual(len(loud["findings"]), 2)
        no_storm = run_panel(RUN, {**performance, "storm": None}, [], [])
        self.assertEqual(no_storm["metrics"]["storm_bias_minutes"], "n/a")


class TestDashboardRoute(unittest.TestCase):
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
        orders = [generate_order(start + timedelta(days=2*i), order_id=f"APIDB-{i}",
                                 cancellation_probability=0) for i in range(120)]
        data = cls.directory / "training.json"
        data.write_text(json.dumps(orders), encoding="utf-8")
        refit_eta(data, cls.artifact, observed_at=datetime(2026, 9, 3, tzinfo=timezone.utc))
        confirmed = datetime(2026, 8, 4, 14, 0, tzinfo=timezone.utc)
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            insert_run(conn, run_id=RUN, source_sha256="dash", source_order_count=1,
                       scenario={}, code_commit="t", image_id="t",
                       model_sha256="m", model_metadata_sha256="mm")
            insert_prediction(conn, run_id=RUN, order_id="D1",
                              request_payload={"order_id": "D1"}, features=dict(FEATURES),
                              predicted_delivery_duration_minutes=40.0, model_sha256="m",
                              predicted_at_simulated=confirmed, model_latency_ms=1.0)
            insert_outcome(conn, run_id=RUN, order_id="D1", confirmed_at=confirmed,
                           promised_delivery_at=confirmed + timedelta(minutes=45),
                           status="delivered", delivered_at=confirmed + timedelta(minutes=50),
                           delivery_duration_minutes=50.0, late_delivery=True,
                           observed_at_simulated=confirmed + timedelta(hours=1))
        cls.addClassCleanup(cls.delete_scratch_rows)

    @classmethod
    def delete_scratch_rows(cls):
        with psycopg.connect(**db_kwargs("POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD")) as conn:
            with conn.cursor() as cur:
                for table in ("predictions", "outcomes", "runs"):
                    cur.execute(f"DELETE FROM app.{table} WHERE run_id = %s;", (RUN,))
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT (SELECT count(*) FROM app.predictions WHERE run_id = %s) + "
                            "(SELECT count(*) FROM app.outcomes WHERE run_id = %s);", (RUN, RUN))
                leftovers = cur.fetchone()[0]
        if leftovers:
            raise AssertionError(f"{leftovers} scratch dashboard rows were not cleaned up")

    def test_dashboard_lists_run_scores(self):
        with TestClient(create_app(self.artifact)) as client:
            response = client.get("/dashboard")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("text/html", response.headers["content-type"])
            self.assertIn(RUN, response.text)
            self.assertIn("10.0", response.text)  # |40 - 50| MAE over one pair.

    def test_dashboard_without_database_is_503(self):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("POSTGRES_")}
        with patch.dict(os.environ, clean, clear=True):
            with TestClient(create_app(self.artifact)) as client:
                self.assertEqual(client.get("/dashboard").status_code, 503)


if __name__ == "__main__":
    unittest.main()
