"""Replay a source window through the live prediction API, then score it.

Orders go in confirmation order with run headers on every request. Outcomes
are ingested cutoff by cutoff: an outcome is admitted only once its delivery
is strictly older than the current cutoff, so nothing is ever scored from the
future. Cancellations are counted, never ingested: no cancellation timing
exists to admit them by. Commits after every order, so rerunning the same run
resumes through idempotent retries instead of duplicating rows.
"""

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx2
import psycopg

from models.baselines import compute_metrics
from models.late_risk import promise_minutes
from models.predict_eta import REQUEST_FIELDS
from models.refit_eta import file_sha256
from persistence.db import db_config_from_env
from persistence.outcomes import insert_outcome
from persistence.predictions import insert_run
from prep.dataset_validation import MARKET_TIMEZONE, parse_timestamp


def fingerprint_source(orders: list[dict]) -> dict:
    """Reject duplicate order IDs; return the run-manifest source identity."""
    seen = set()
    for order in orders:
        order_id = order["order_id"]
        if order_id in seen:
            raise ValueError(f"Duplicate order_id in source: {order_id}")
        seen.add(order_id)
    canonical = json.dumps(orders, sort_keys=True, default=str).encode("utf-8")
    return {"source_sha256": hashlib.sha256(canonical).hexdigest(),
            "source_order_count": len(orders)}


def register_run(conn, *, run_id: str, source_orders: list[dict], source_name: str,
                 code_commit: str, image_id: str,
                 model_sha256: str, model_metadata_sha256: str) -> dict:
    """Store the experiment manifest; the caller commits it before replaying."""
    source = fingerprint_source(source_orders)
    scenario = {"source_file": source_name,
                "confirmation_window": [min(o["confirmed_at"] for o in source_orders),
                                        max(o["confirmed_at"] for o in source_orders)]}
    return insert_run(conn, run_id=run_id, source_sha256=source["source_sha256"],
                      source_order_count=source["source_order_count"], scenario=scenario,
                      code_commit=code_commit, image_id=image_id,
                      model_sha256=model_sha256, model_metadata_sha256=model_metadata_sha256)


def post_prediction(client: httpx2.Client, order: dict, run_id: str) -> dict:
    """POST one confirmation-time request; every status except 200 is loud."""
    payload = {field: order[field] for field in REQUEST_FIELDS}
    response = client.post("/predict", json=payload,
                           headers={"X-Run-Id": run_id, "X-Predicted-At": order["confirmed_at"]})
    if response.status_code == 409:
        raise ValueError(f"Conflicting stored prediction for {(run_id, order['order_id'])}; "
                         "source changed under a reused run_id")
    if response.status_code != 200:
        raise ValueError(f"Prediction for {order['order_id']} failed "
                         f"with HTTP {response.status_code}: {response.text[:200]}")
    return response.json()


def run_model_sha256(client: httpx2.Client) -> str:
    health = client.get("/health")
    if health.status_code != 200:
        raise ValueError(f"API not ready: HTTP {health.status_code}")
    return health.json()["model_sha256"]


def ingest_newly_available(conn, *, run_id: str, source_by_id: dict,
                           ingested: set, observed_at: datetime) -> int:
    """Store every not-yet-ingested delivery strictly older than the cutoff."""
    admitted = 0
    for order_id, order in source_by_id.items():
        if order_id in ingested or order["status"] != "delivered":
            continue
        available_at = parse_timestamp(order["delivered_at"])
        if not available_at < observed_at:
            continue
        confirmed_at = parse_timestamp(order["confirmed_at"])
        promised_at = parse_timestamp(order["promised_delivery_at"])
        insert_outcome(conn, run_id=run_id, order_id=order_id, confirmed_at=confirmed_at,
                       promised_delivery_at=promised_at, status="delivered",
                       delivered_at=available_at,
                       delivery_duration_minutes=order["delivery_duration_minutes"],
                       late_delivery=order["late_delivery"],
                       observed_at_simulated=observed_at)
        ingested.add(order_id)
        admitted += 1
    return admitted


def replay(client: httpx2.Client, conn, *, run_id: str, source_orders: list[dict],
           model_sha256: str) -> dict:
    """Request every order in confirmation order; ingest outcomes as cutoffs pass."""
    ordered = sorted(source_orders, key=lambda o: parse_timestamp(o["confirmed_at"]))
    source_by_id = {o["order_id"]: o for o in ordered}
    ingested: set[str] = set()
    predicted = 0
    for order in ordered:
        response = post_prediction(client, order, run_id)
        if response["model_sha256"] != model_sha256:
            raise ValueError("Server answered with a different model than this run registered")
        predicted += 1
        cutoff = parse_timestamp(order["confirmed_at"])
        ingest_newly_available(conn, run_id=run_id, source_by_id=source_by_id,
                               ingested=ingested, observed_at=cutoff)
        conn.commit()
    latest = max(parse_timestamp(o["delivered_at"]) for o in ordered if o["status"] == "delivered")
    ingest_newly_available(conn, run_id=run_id, source_by_id=source_by_id,
                           ingested=ingested, observed_at=latest + timedelta(minutes=1))
    conn.commit()
    return {"predicted": predicted, "ingested_outcomes": len(ingested)}


def score_run(conn, *, run_id: str, source_orders: list[dict]) -> dict:
    """Join stored predictions to stored outcomes; pending labels are reported, not scored."""
    source_by_id = {o["order_id"]: o for o in source_orders}
    with conn.cursor() as cur:
        cur.execute("SELECT order_id, request_payload, predicted_delivery_duration_minutes, "
                    "late_probability FROM app.predictions WHERE run_id = %s;", (run_id,))
        predictions = cur.fetchall()
        cur.execute("SELECT order_id, delivery_duration_minutes, observed_at_simulated "
                    "FROM app.outcomes WHERE run_id = %s;", (run_id,))
        outcomes = {o: (d, obs) for o, d, obs in cur.fetchall()}
    matched_actual, matched_pred = [], []
    risk_actual, risk_pred = [], []
    pending, cancelled_predictions, missing_prediction = 0, 0, 0
    for order_id, payload, predicted, probability in predictions:
        if payload.get("confirmed_at") != source_by_id.get(order_id, {}).get("confirmed_at"):
            raise ValueError(f"Stored prediction for {order_id} disagrees with the source")
        if order_id in outcomes:
            matched_actual.append(outcomes[order_id][0])
            matched_pred.append(float(predicted))
            if probability is not None:
                risk_actual.append(1.0 if outcomes[order_id][0] > promise_minutes(source_by_id[order_id]) else 0.0)
                risk_pred.append(float(probability))
        elif source_by_id.get(order_id, {}).get("status") == "cancelled":
            cancelled_predictions += 1
        else:
            pending += 1
    observed_without_prediction = sum(
        1 for o in source_by_id if o not in {p[0] for p in predictions}
        and source_by_id[o]["status"] == "delivered" and o in outcomes)
    observed_at = [obs for _, obs in outcomes.values()]
    return {
        "run_id": run_id,
        "source_orders": len(source_orders),
        "successful_predictions": len(predictions),
        "matched_deliveries": len(matched_actual),
        "predictions_awaiting_outcome": pending,
        "predictions_for_cancelled_orders": cancelled_predictions,
        "observed_deliveries_without_prediction": observed_without_prediction,
        "observed_cancellations": sum(1 for o in source_by_id.values() if o["status"] == "cancelled"),
        "observation_cutoff": max(observed_at).isoformat() if observed_at else None,
        "metrics": compute_metrics(matched_actual, matched_pred) if matched_actual else None,
        "risk": {"brier": round(sum((p - y) ** 2 for y, p in zip(risk_actual, risk_pred))
                                / len(risk_actual), 4) if risk_actual else None,
                 "scored": len(risk_actual),
                 "missing_probability": len(matched_actual) - len(risk_actual)}
        if matched_actual else None,
        "simulated": True,
    }


def git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unrecorded"


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a source window through the live ETA API.")
    parser.add_argument("--source", type=Path, required=True, help="Source orders JSON file.")
    parser.add_argument("--run-id", required=True, help="New experiment ID; reruns resume it.")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model-dir", type=Path, required=True, help="Trusted artifact for metadata hash.")
    parser.add_argument("--start", type=str, default=None, help="Toronto confirmation date YYYY-MM-DD.")
    parser.add_argument("--end", type=str, default=None, help="Toronto confirmation date YYYY-MM-DD.")
    parser.add_argument("--image-id", default="local-direct")
    args = parser.parse_args()
    try:
        orders = json.loads(args.source.read_text(encoding="utf-8"))
        if args.start or args.end:
            orders = [o for o in orders
                      if (not args.start or parse_timestamp(o["confirmed_at"])
                          .astimezone(MARKET_TIMEZONE).date().isoformat() >= args.start)
                      and (not args.end or parse_timestamp(o["confirmed_at"])
                           .astimezone(MARKET_TIMEZONE).date().isoformat() <= args.end)]
        if not orders:
            raise ValueError("No source orders in the selected window")
        metadata = json.loads((args.model_dir / "metadata.json").read_text(encoding="utf-8"))
        config = db_config_from_env()
        if config is None:
            raise ValueError("POSTGRES_DB/APP_USER/APP_PASSWORD must be set to replay")
        with httpx2.Client(base_url=args.api_url, timeout=30) as client:
            server_model = run_model_sha256(client)
            if server_model != metadata["model_sha256"]:
                raise ValueError("Server model differs from the selected artifact")
            with psycopg.connect(**config) as conn:
                register_run(conn, run_id=args.run_id, source_orders=orders,
                             source_name=args.source.name, code_commit=git_head(),
                             image_id=args.image_id, model_sha256=metadata["model_sha256"],
                             model_metadata_sha256=file_sha256(args.model_dir / "metadata.json"))
                conn.commit()
                replay(client, conn, run_id=args.run_id, source_orders=orders,
                       model_sha256=metadata["model_sha256"])
                report = score_run(conn, run_id=args.run_id, source_orders=orders)
        print(json.dumps(report, indent=2))
    except Exception as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
