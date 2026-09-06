"""Rollback-wrapped integration tests for durable prediction logging.

Needs local PostgreSQL with migration 001 applied; credentials travel via
the environment so no secret is committed. From fish:

    env (grep -E '^POSTGRES_(DB|APP_USER|APP_PASSWORD)=' .env) \
        .venv/bin/python -W error -m unittest tests.test_prediction_logging -v

Without those variables every test skips and the database is untouched.
Connections never commit: cleanup rolls back, so scratch rows never persist.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone

import psycopg

from persistence.outcomes import insert_outcome
from persistence.predictions import insert_prediction, insert_run

RUN = "TEST-LOGGING"
MANIFEST = {
    "source_sha256": "abc123",
    "source_order_count": 2,
    "scenario": {"seed": 42},
    "code_commit": "c0ffee",
    "image_id": "img-1",
    "model_sha256": "model-1",
    "model_metadata_sha256": "meta-1",
}
PAYLOAD = {"order_id": "O1", "confirmed_at": "2026-09-03T18:00:00-04:00"}
FEATURES = {"distance_km": 2.5, "local_hour": 18}
PREDICTED_AT = datetime(2026, 9, 3, 22, 0, tzinfo=timezone.utc)
CONFIRMED = datetime(2026, 9, 3, 14, 0, tzinfo=timezone.utc)  # 10:00 EDT.
PROMISED = CONFIRMED + timedelta(minutes=45)
DELIVERED = CONFIRMED + timedelta(minutes=50)
OBSERVED = DELIVERED + timedelta(minutes=10)


def prediction_args(**overrides):
    args = {
        "run_id": RUN,
        "order_id": "O1",
        "request_payload": dict(PAYLOAD),
        "features": dict(FEATURES),
        "predicted_delivery_duration_minutes": 43.63,
        "model_sha256": "model-1",
        "predicted_at_simulated": PREDICTED_AT,
        "model_latency_ms": 1.5,
    }
    args.update(overrides)
    return args


class TestPredictionLogging(unittest.TestCase):
    def setUp(self):
        try:
            dbname = os.environ["POSTGRES_DB"]
            user = os.environ["POSTGRES_APP_USER"]
            password = os.environ["POSTGRES_APP_PASSWORD"]
        except KeyError:
            self.skipTest("POSTGRES_DB/APP_USER/APP_PASSWORD not set; local DB tests skipped")
        self.conn = psycopg.connect(host=os.environ.get("PGHOST", "127.0.0.1"),
                                    port=os.environ.get("PGPORT", "5432"),
                                    dbname=dbname, user=user, password=password)
        self.addCleanup(self.conn.close)
        self.conn.rollback()
        self.addCleanup(self.conn.rollback)
        insert_run(self.conn, run_id=RUN, **MANIFEST)

    def count(self, table, run_id=RUN):
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM app.{table} WHERE run_id = %s;", (run_id,))
            return cur.fetchone()[0]

    def test_run_retry_returns_same_row_without_duplicate(self):
        first = insert_run(self.conn, run_id=RUN, **MANIFEST)
        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(insert_run(self.conn, run_id=RUN, **MANIFEST)["created_at_wall"],
                         first["created_at_wall"])

    def test_conflicting_run_manifest_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "Conflicting run manifest"):
            insert_run(self.conn, run_id=RUN, **{**MANIFEST, "model_sha256": "other-model"})
        self.assertEqual(self.count("runs"), 1)

    def test_prediction_retry_keeps_first_write(self):
        first = insert_prediction(self.conn, **prediction_args())
        self.assertEqual(self.count("predictions"), 1)
        retry = insert_prediction(self.conn, **prediction_args(model_latency_ms=99.0))
        self.assertEqual(self.count("predictions"), 1)
        self.assertEqual(retry["predicted_delivery_duration_minutes"], 43.63)
        self.assertEqual(retry["recorded_at_wall"], first["recorded_at_wall"])
        self.assertEqual(retry["model_latency_ms"], 1.5)

    def test_conflicting_predictions_are_errors(self):
        insert_prediction(self.conn, **prediction_args())
        conflicts = {
            "payload": prediction_args(request_payload={"order_id": "O1", "distance_km": 9.9}),
            "model": prediction_args(model_sha256="other-model"),
            "value": prediction_args(predicted_delivery_duration_minutes=10.0),
        }
        for name, args in conflicts.items():
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Conflicting prediction"):
                insert_prediction(self.conn, **args)
        self.assertEqual(self.count("predictions"), 1)

    def test_failed_writes_raise_and_store_nothing(self):
        # Each failure poisons its transaction, so isolate with savepoints —
        # the same pattern the future API uses before returning a service error.
        failures = {
            "floor": prediction_args(predicted_delivery_duration_minutes=1.0),
            "unknown-run": prediction_args(run_id="NO-SUCH-RUN"),
        }
        for name, args in failures.items():
            with self.subTest(name=name):
                with self.conn.cursor() as cur:
                    cur.execute("SAVEPOINT failed_write;")
                    with self.assertRaises(psycopg.Error):
                        insert_prediction(self.conn, **args)
                    cur.execute("ROLLBACK TO SAVEPOINT failed_write;")
        self.assertEqual(self.count("predictions"), 0)

    def test_runs_and_orders_stay_separate(self):
        insert_prediction(self.conn, **prediction_args())
        insert_run(self.conn, run_id="TEST-OTHER", **MANIFEST)
        insert_prediction(self.conn, **prediction_args(run_id="TEST-OTHER", order_id="O2",
                                                             request_payload={**PAYLOAD, "order_id": "O2"}))
        self.assertEqual(self.count("predictions"), 1)
        self.assertEqual(self.count("predictions", "TEST-OTHER"), 1)

    def outcome_args(self, **overrides):
        args = {
            "run_id": RUN,
            "order_id": "O1",
            "confirmed_at": CONFIRMED,
            "promised_delivery_at": PROMISED,
            "status": "delivered",
            "delivered_at": DELIVERED,
            "delivery_duration_minutes": 50.0,
            "late_delivery": True,
            "observed_at_simulated": OBSERVED,
        }
        args.update(overrides)
        return args

    def test_outcome_reingest_keeps_first_observation(self):
        first = insert_outcome(self.conn, **self.outcome_args())
        self.assertEqual(self.count("outcomes"), 1)
        retry = insert_outcome(self.conn, **self.outcome_args(
            observed_at_simulated=OBSERVED + timedelta(hours=1)))
        self.assertEqual(self.count("outcomes"), 1)
        self.assertEqual(retry["observed_at_simulated"], first["observed_at_simulated"])
        self.assertEqual(retry["recorded_at_wall"], first["recorded_at_wall"])

    def test_conflicting_outcome_labels_raise(self):
        insert_outcome(self.conn, **self.outcome_args())
        later = DELIVERED + timedelta(minutes=5)
        with self.assertRaisesRegex(ValueError, "Conflicting outcome"):
            insert_outcome(self.conn, **self.outcome_args(
                delivered_at=later, delivery_duration_minutes=55.0,
                observed_at_simulated=later + timedelta(minutes=10)))
        self.assertEqual(self.count("outcomes"), 1)

    def test_impossible_outcomes_raise(self):
        cases = {
            "naive-confirmed": self.outcome_args(confirmed_at=CONFIRMED.replace(tzinfo=None)),
            "not-after-confirmation": self.outcome_args(
                delivered_at=CONFIRMED, delivery_duration_minutes=0.0, late_delivery=False,
                observed_at_simulated=CONFIRMED + timedelta(minutes=1)),
            "duration-mismatch": self.outcome_args(delivery_duration_minutes=51.0),
            "wrong-late-flag": self.outcome_args(late_delivery=False),
            "observed-at-availability": self.outcome_args(observed_at_simulated=DELIVERED),
        }
        for name, args in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                insert_outcome(self.conn, **args)
        self.assertEqual(self.count("outcomes"), 0)

    def test_cancelled_outcome_needs_an_observation_policy(self):
        with self.assertRaisesRegex(ValueError, "observation policy"):
            insert_outcome(self.conn, **self.outcome_args(
                status="cancelled", delivered_at=None, delivery_duration_minutes=None,
                late_delivery=None))
        self.assertEqual(self.count("outcomes"), 0)

    def test_outcome_without_prediction_is_allowed(self):
        row = insert_outcome(self.conn, **self.outcome_args(order_id="O9"))
        self.assertEqual(row["delivery_duration_minutes"], 50.0)
        self.assertEqual(self.count("outcomes"), 1)
        self.assertEqual(self.count("predictions"), 0)

    def test_outcome_for_unknown_run_raises(self):
        with self.conn.cursor() as cur:
            cur.execute("SAVEPOINT unknown_run;")
            with self.assertRaises(psycopg.Error):
                insert_outcome(self.conn, **self.outcome_args(run_id="NO-SUCH-RUN"))
            cur.execute("ROLLBACK TO SAVEPOINT unknown_run;")
        self.assertEqual(self.count("outcomes"), 0)


if __name__ == "__main__":
    unittest.main()
