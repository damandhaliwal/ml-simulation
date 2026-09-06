"""Replay tests: tiny hand-checkable window through TestClient + real Postgres.

Needs the local stack with migration 001 applied. App credentials drive the
harness; admin credentials are used only by class cleanup to delete scratch
rows. From fish:

    env (grep -E '^POSTGRES_(DB|ADMIN_USER|ADMIN_PASSWORD|APP_USER|APP_PASSWORD)=' .env) \
        .venv/bin/python -W error -m unittest tests.test_replay -v

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

from models.baselines import compute_metrics
from models.refit_eta import refit_eta
from models.refit_risk import refit_risk
from persistence.predictions import insert_run
from prep.dataset_validation import parse_timestamp
from replay.harness import fingerprint_source, register_run, replay, score_run
from serving.api import create_app
from simulator.generate_orders import generate_order

RUN = "TEST-REPLAY"
APP_KEYS = ("POSTGRES_DB", "POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")
ADMIN_KEYS = ("POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD")


def db_kwargs(user_key, password_key):
    return {"host": os.environ.get("PGHOST", "127.0.0.1"), "port": int(os.environ.get("PGPORT", "5432")),
            "dbname": os.environ["POSTGRES_DB"], "user": os.environ[user_key],
            "password": os.environ[password_key]}


def tiny_window():
    """Eight orders over two Toronto afternoons; two cancel. Deterministic."""
    base = datetime.fromisoformat("2026-08-04T14:00:00-04:00")
    orders = []
    for i in range(8):
        orders.append(generate_order(base + timedelta(minutes=7 * i), order_id=f"REPLAY-{i}",
                                     seed=7, cancellation_probability=0))
    for i in (2, 5):
        orders[i] = generate_order(base + timedelta(minutes=7 * i), order_id=f"REPLAY-{i}",
                                   seed=7, cancellation_probability=1)
        assert orders[i]["status"] == "cancelled", orders[i]
    assert sum(1 for o in orders if o["status"] == "delivered") == 6
    return orders


class TestReplay(unittest.TestCase):
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
        training = [generate_order(start + timedelta(days=2*i), order_id=f"RP-{i}",
                                   cancellation_probability=0) for i in range(120)]
        data = cls.directory / "training.json"
        data.write_text(json.dumps(training), encoding="utf-8")
        refit_eta(data, cls.artifact, observed_at=datetime(2026, 9, 3, tzinfo=timezone.utc))
        cls.model_sha256 = json.loads((cls.artifact / "metadata.json").read_text(encoding="utf-8"))["model_sha256"]
        cls.risk_artifact = cls.directory / "risk-artifact"
        refit_risk(data, cls.risk_artifact, observed_at=datetime(2026, 9, 3, tzinfo=timezone.utc))
        cls.orders = tiny_window()
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            register_run(conn, run_id=RUN, source_orders=cls.orders, source_name="tiny-test",
                         code_commit="test", image_id="test-direct",
                         model_sha256=cls.model_sha256, model_metadata_sha256="meta-1")
        cls.addClassCleanup(cls.delete_scratch_rows)

    @classmethod
    def delete_one_run(cls, run_id):
        with psycopg.connect(**db_kwargs("POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD")) as conn:
            with conn.cursor() as cur:
                for table in ("predictions", "outcomes", "runs"):
                    cur.execute(f"DELETE FROM app.{table} WHERE run_id = %s;", (run_id,))

    @classmethod
    def delete_scratch_rows(cls):
        cls.delete_one_run(RUN)
        cls.delete_one_run("TEST-CHANGED")
        cls.delete_one_run("TEST-RISK-RP")
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT (SELECT count(*) FROM app.predictions WHERE run_id = %s) + "
                            "(SELECT count(*) FROM app.outcomes WHERE run_id = %s);", (RUN, RUN))
                leftovers = cur.fetchone()[0]
        if leftovers:
            raise AssertionError(f"{leftovers} scratch replay rows were not cleaned up")

    def replay_run(self, run_id=RUN, orders=None):
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            with TestClient(create_app(self.artifact)) as client:
                result = replay(client, conn, run_id=run_id,
                                source_orders=self.orders if orders is None else orders,
                                model_sha256=self.model_sha256)
            with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as scoring:
                report = score_run(scoring, run_id=run_id,
                                   source_orders=self.orders if orders is None else orders)
        return result, report

    def test_replay_scores_everything_and_counts_cancellations(self):
        _, report = self.replay_run()
        self.assertEqual(report["source_orders"], 8)
        self.assertEqual(report["successful_predictions"], 8)
        self.assertEqual(report["matched_deliveries"], 6)
        self.assertEqual(report["predictions_awaiting_outcome"], 0)
        self.assertEqual(report["predictions_for_cancelled_orders"], 2)
        self.assertEqual(report["observed_deliveries_without_prediction"], 0)
        self.assertEqual(report["observed_cancellations"], 2)
        latest = max(parse_timestamp(o["delivered_at"]) for o in self.orders if o["status"] == "delivered")
        self.assertEqual(report["observation_cutoff"], (latest + timedelta(minutes=1)).isoformat())
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT p.predicted_delivery_duration_minutes, o.delivery_duration_minutes "
                            "FROM app.predictions p JOIN app.outcomes o USING (run_id, order_id) "
                            "WHERE p.run_id = %s;", (RUN,))
                pairs = cur.fetchall()
        actual = [float(d) for _, d in pairs]
        predicted = [float(p) for p, _ in pairs]
        self.assertEqual(report["metrics"], compute_metrics(actual, predicted))

    def test_risk_brier_is_scored_when_served(self):
        run_id = "TEST-RISK-RP"
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            register_run(conn, run_id=run_id, source_orders=self.orders, source_name="tiny-risk",
                         code_commit="test", image_id="test-direct",
                         model_sha256=self.model_sha256, model_metadata_sha256="meta-1")
            conn.commit()  # The API reads through its own connection, so setup must commit.
            with TestClient(create_app(self.artifact, self.risk_artifact)) as client:
                replay(client, conn, run_id=run_id, source_orders=self.orders,
                       model_sha256=self.model_sha256)
            report = score_run(conn, run_id=run_id, source_orders=self.orders)
        self.assertEqual(report["risk"]["scored"], 6)
        self.assertEqual(report["risk"]["missing_probability"], 0)
        self.assertTrue(0.0 <= report["risk"]["brier"] <= 1.0)
        self.delete_one_run(run_id)

    def test_rerun_is_identical(self):
        _, first = self.replay_run()
        _, second = self.replay_run()
        self.assertEqual(second, first)

    def test_changed_source_under_same_run_raises(self):
        run_id = "TEST-CHANGED"
        manifest = {"source_sha256": "changed-test", "source_order_count": 8,
                    "scenario": {"test": True}, "code_commit": "t", "image_id": "t",
                    "model_sha256": self.model_sha256, "model_metadata_sha256": "meta-1"}
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            insert_run(conn, run_id=run_id, **manifest)
            conn.commit()  # The API reads through its own connection, so setup must commit.
            with TestClient(create_app(self.artifact)) as client:
                replay(client, conn, run_id=run_id, source_orders=self.orders,
                       model_sha256=self.model_sha256)
                changed = [dict(o, distance_km=9.99) if o["order_id"] == "REPLAY-0" else o
                           for o in self.orders]
                with self.assertRaisesRegex(ValueError, "Conflicting stored prediction"):
                    replay(client, conn, run_id=run_id, source_orders=changed,
                           model_sha256=self.model_sha256)
        self.delete_one_run(run_id)

    def test_duplicate_source_ids_are_rejected_before_any_request(self):
        with psycopg.connect(**db_kwargs("POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")) as conn:
            with self.assertRaisesRegex(ValueError, "Duplicate order_id"):
                register_run(conn, run_id="TEST-DUP", source_orders=self.orders + [self.orders[0]],
                             source_name="dup", code_commit="t", image_id="t",
                             model_sha256="m", model_metadata_sha256="mm")


if __name__ == "__main__":
    unittest.main()
