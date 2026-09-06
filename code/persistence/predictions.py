from datetime import datetime
from math import isfinite
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Json

RUN_COMPARE_FIELDS = (
    "source_sha256",
    "source_order_count",
    "scenario",
    "code_commit",
    "image_id",
    "model_sha256",
    "model_metadata_sha256",
    "simulated",
)


class PredictionConflict(ValueError):
    """Same key, different content: retry was not identical. Never overwrite."""


def _require_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _require_aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def insert_run(
    conn: Connection,
    *,
    run_id: str,
    source_sha256: str,
    source_order_count: int,
    scenario: dict,
    code_commit: str,
    image_id: str,
    model_sha256: str,
    model_metadata_sha256: str,
    simulated: bool = True,
) -> dict[str, Any]:
    """Insert one run manifest; an identical retry returns the stored row.

    A different manifest under the same run_id is a ValueError, never an
    overwrite. Executes without committing so the caller can commit before
    responding (or roll back in tests).
    """
    _require_id(run_id, "run_id")
    if not isinstance(scenario, dict):
        raise ValueError("scenario must be a dict")
    supplied = {
        "source_sha256": source_sha256,
        "source_order_count": source_order_count,
        "scenario": scenario,
        "code_commit": code_commit,
        "image_id": image_id,
        "model_sha256": model_sha256,
        "model_metadata_sha256": model_metadata_sha256,
        "simulated": simulated,
    }
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """INSERT INTO app.runs
               (run_id, source_sha256, source_order_count, scenario,
                code_commit, image_id, model_sha256, model_metadata_sha256, simulated)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (run_id) DO NOTHING RETURNING *;""",
            (run_id, source_sha256, source_order_count, Json(scenario),
             code_commit, image_id, model_sha256, model_metadata_sha256, simulated),
        )
        row = cur.fetchone()
        if row is not None:
            return row
        cur.execute("SELECT * FROM app.runs WHERE run_id = %s;", (run_id,))
        existing = cur.fetchone()
        # The row must exist: nobody holds DELETE and we just attempted it.
        if {k: existing[k] for k in RUN_COMPARE_FIELDS} != supplied:
            raise PredictionConflict(f"Conflicting run manifest for run_id {run_id!r}")
        return existing


def insert_prediction(
    conn: Connection,
    *,
    run_id: str,
    order_id: str,
    request_payload: dict,
    features: dict,
    predicted_delivery_duration_minutes: float,
    model_sha256: str,
    predicted_at_simulated: datetime,
    model_latency_ms: float,
    recorded_at_wall: datetime | None = None,
    late_probability: float | None = None,
    simulated: bool = True,
) -> dict[str, Any]:
    """Insert one logical prediction; an identical retry returns the stored row.

    Attempt-specific timing (recorded_at_wall, model_latency_ms) is kept from
    the first write, never overwritten. A different payload, features, model,
    predicted value, or risk probability under the same key is a ValueError.
    A None probability marks a pre-risk row and matches only None. Constraint
    violations (unknown run, floored duration) propagate as database errors
    instead of a fake successful row. No commit; the caller commits first.
    """
    _require_id(run_id, "run_id")
    _require_id(order_id, "order_id")
    if not isinstance(request_payload, dict) or request_payload.get("order_id") != order_id:
        raise ValueError("request_payload must be a dict whose order_id matches")
    if not isinstance(features, dict):
        raise ValueError("features must be a dict")
    for name, value in (("predicted_delivery_duration_minutes", predicted_delivery_duration_minutes),
                        ("model_latency_ms", model_latency_ms)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
            raise ValueError(f"{name} must be a finite number")
    _require_id(model_sha256, "model_sha256")
    _require_aware(predicted_at_simulated, "predicted_at_simulated")
    if recorded_at_wall is not None:
        _require_aware(recorded_at_wall, "recorded_at_wall")
    if late_probability is not None and (
            not isinstance(late_probability, (int, float))
            or isinstance(late_probability, bool)
            or not 0.0 <= late_probability <= 1.0):
        raise ValueError("late_probability must be None or a number in [0, 1]")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """INSERT INTO app.predictions
               (run_id, order_id, request_payload, features,
                predicted_delivery_duration_minutes, model_sha256,
                predicted_at_simulated, recorded_at_wall, model_latency_ms,
                late_probability, simulated)
               VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()), %s, %s, %s)
               ON CONFLICT (run_id, order_id) DO NOTHING RETURNING *;""",
            (run_id, order_id, Json(request_payload), Json(features),
             predicted_delivery_duration_minutes, model_sha256,
             predicted_at_simulated, recorded_at_wall, model_latency_ms,
             late_probability, simulated),
        )
        row = cur.fetchone()
        if row is not None:
            return row
        cur.execute(
            "SELECT * FROM app.predictions WHERE run_id = %s AND order_id = %s;",
            (run_id, order_id),
        )
        existing = cur.fetchone()
        stored_probability = existing["late_probability"]
        probabilities_match = (stored_probability is None and late_probability is None) or (
            stored_probability is not None and late_probability is not None
            and float(stored_probability) == float(late_probability))
        if (existing["request_payload"] != request_payload
                or existing["features"] != features
                or existing["model_sha256"] != model_sha256
                or float(existing["predicted_delivery_duration_minutes"])
                != float(predicted_delivery_duration_minutes)
                or not probabilities_match):
            raise PredictionConflict(f"Conflicting prediction for {(run_id, order_id)!r}")
        return existing
