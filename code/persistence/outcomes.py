from datetime import datetime
from math import isfinite
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

# Same timestamp/duration agreement tolerance as prep.dataset_validation.
DELIVERY_TOLERANCE_MINUTES = 1e-6


def _require_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _require_aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def insert_outcome(
    conn: Connection,
    *,
    run_id: str,
    order_id: str,
    confirmed_at: datetime,
    promised_delivery_at: datetime,
    status: str,
    delivered_at: datetime | None = None,
    delivery_duration_minutes: float | None = None,
    late_delivery: bool | None = None,
    observed_at_simulated: datetime,
    recorded_at_wall: datetime | None = None,
    simulated: bool = True,
) -> dict[str, Any]:
    """Insert one terminal delivery outcome; an identical re-ingest is a no-op.

    The same labels admitted again keep the first observation/recording times.
    Different labels under the same key are a ValueError, never an overwrite.
    Cancellations are refused until an observation policy is agreed (see
    docs/prediction-logging.md): there is no cancelled_at to derive
    availability from, and none is invented here. No prediction row is
    required. Executes without committing; the caller owns the transaction.
    """
    _require_id(run_id, "run_id")
    _require_id(order_id, "order_id")
    _require_aware(confirmed_at, "confirmed_at")
    _require_aware(promised_delivery_at, "promised_delivery_at")
    _require_aware(observed_at_simulated, "observed_at_simulated")
    if recorded_at_wall is not None:
        _require_aware(recorded_at_wall, "recorded_at_wall")
    if status == "cancelled":
        raise ValueError("Cancelled outcomes need an agreed observation policy first")
    if status != "delivered":
        raise ValueError(f"Unknown outcome status: {status}")
    _require_aware(delivered_at, "delivered_at")
    if not isinstance(delivery_duration_minutes, (int, float)) \
            or isinstance(delivery_duration_minutes, bool) \
            or not isfinite(delivery_duration_minutes) or delivery_duration_minutes <= 0:
        raise ValueError("delivery_duration_minutes must be a finite positive number")
    if not isinstance(late_delivery, bool):
        raise ValueError("late_delivery must be a boolean")
    if delivered_at <= confirmed_at:
        raise ValueError("delivered_at must be after confirmed_at")
    elapsed = (delivered_at - confirmed_at).total_seconds() / 60
    if abs(elapsed - delivery_duration_minutes) > DELIVERY_TOLERANCE_MINUTES:
        raise ValueError("delivery_duration_minutes disagrees with the delivery timestamps")
    if late_delivery != (delivered_at > promised_delivery_at):
        raise ValueError("late_delivery must compare delivery against the original promise")
    if not delivered_at < observed_at_simulated:
        raise ValueError("Outcome is only admissible strictly after it becomes available")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """INSERT INTO app.outcomes
               (run_id, order_id, confirmed_at, promised_delivery_at, status,
                delivered_at, delivery_duration_minutes, late_delivery,
                outcome_available_at_simulated, observed_at_simulated,
                recorded_at_wall, simulated)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()), %s)
               ON CONFLICT (run_id, order_id) DO NOTHING RETURNING *;""",
            (run_id, order_id, confirmed_at, promised_delivery_at, status,
             delivered_at, delivery_duration_minutes, late_delivery,
             delivered_at, observed_at_simulated, recorded_at_wall, simulated),
        )
        row = cur.fetchone()
        if row is not None:
            return row
        cur.execute(
            "SELECT * FROM app.outcomes WHERE run_id = %s AND order_id = %s;",
            (run_id, order_id),
        )
        existing = cur.fetchone()
        labels = (existing["confirmed_at"], existing["promised_delivery_at"], existing["status"],
                  existing["delivered_at"], float(existing["delivery_duration_minutes"]),
                  existing["late_delivery"])
        if labels != (confirmed_at, promised_delivery_at, status,
                      delivered_at, float(delivery_duration_minutes), late_delivery):
            raise ValueError(f"Conflicting outcome for {(run_id, order_id)!r}")
        return existing
