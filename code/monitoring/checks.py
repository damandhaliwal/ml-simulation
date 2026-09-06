"""Input-drift and observed-performance checks for logged replay runs.

Two questions, kept separate on purpose: did the inputs change (drift), and
did the predictions get worse (degradation)? October's storm shift is designed
to answer them differently — inputs barely move while storm errors jump — so a
monitor that conflates the two fails this suite visibly.

Thresholds below are learning thresholds for synthetic data, not business
gates. They live in one place (ALERT_THRESHOLD_*) so a real deployment can
replace them with agreed service levels without touching the math.
"""

import argparse
import json
from pathlib import Path
from statistics import mean, pstdev

import psycopg

from models.baselines import compute_metrics
from persistence.db import db_config_from_env
from prep.dataset_validation import load_orders

# A numeric feature drifts when its run mean moves this many training
# standard deviations; a categorical drifts past this L1 distance.
ALERT_MEAN_SHIFT_STD = 1.0
ALERT_CATEGORY_L1 = 0.10
# Observed performance alerts against the frozen September held-out read.
ALERT_MAE_MINUTES = 4.0
ALERT_STORM_BIAS_MINUTES = -1.0

NUMERIC_SUMMARY = (
    "distance_km",
    "item_count",
    "traffic_index",
    "restaurant_backlog",
    "orders_waiting_for_courier",
    "idle_couriers",
    "precipitation_mm_per_hour",
    "temperature_c",
)
CATEGORICAL_SUMMARY = ("pickup_zone_id", "dropoff_zone_id", "weather_type")


def summarize_feature_rows(rows: list[dict]) -> dict:
    """Mean/std/count per numeric feature and rates per category value."""
    summary: dict = {"n": len(rows), "numeric": {}, "categorical": {}}
    for field in NUMERIC_SUMMARY:
        values = [float(r[field]) for r in rows]
        summary["numeric"][field] = {"mean": mean(values), "std": pstdev(values) or 0.0}
    for field in CATEGORICAL_SUMMARY:
        rates: dict[str, float] = {}
        for r in rows:
            rates[r[field]] = rates.get(r[field], 0) + 1
        summary["categorical"][field] = {k: v / len(rows) for k, v in sorted(rates.items())}
    return summary


def summarize_source_file(path: Path | str) -> dict:
    """Baseline statistics over delivered source rows (prediction-time inputs only)."""
    orders = [o for o in load_orders(path) if o["status"] == "delivered"]
    baseline = summarize_feature_rows(orders)
    baseline["source_rows"] = len(orders)
    return baseline


def summarize_logged_run(conn, run_id: str) -> dict:
    """Same statistics from stored feature snapshots for one run."""
    with conn.cursor() as cur:
        cur.execute("SELECT features FROM app.predictions WHERE run_id = %s;", (run_id,))
        rows = [r[0] for r in cur.fetchall()]
    if not rows:
        raise ValueError(f"No logged predictions for run_id {run_id!r}")
    return summarize_feature_rows(rows)


def check_drift(baseline: dict, current: dict) -> list[dict]:
    """One finding per shifted feature; empty means inputs look familiar."""
    findings = []
    for field, stats in baseline["numeric"].items():
        shift = current["numeric"][field]["mean"] - stats["mean"]
        standardized = shift / stats["std"] if stats["std"] else 0.0
        if abs(standardized) >= ALERT_MEAN_SHIFT_STD:
            findings.append({"dimension": "input", "feature": field,
                             "baseline_mean": round(stats["mean"], 4),
                             "current_mean": round(current["numeric"][field]["mean"], 4),
                             "std_shifts": round(standardized, 3)})
    for field, rates in baseline["categorical"].items():
        current_rates = current["categorical"][field]
        distance = sum(abs(current_rates.get(k, 0.0) - v) for k, v in rates.items())
        distance += sum(v for k, v in current_rates.items() if k not in rates)
        if distance >= ALERT_CATEGORY_L1:
            findings.append({"dimension": "input", "feature": field,
                             "l1_distance": round(distance, 4)})
    return findings


def check_performance(conn, run_id: str) -> tuple[dict, list[dict]]:
    """Matched-pair errors overall and on storms, with alert findings."""
    with conn.cursor() as cur:
        cur.execute("SELECT p.predicted_delivery_duration_minutes, o.delivery_duration_minutes, "
                    "p.request_payload, p.late_probability, o.delivered_at, o.promised_delivery_at "
                    "FROM app.predictions p JOIN app.outcomes o USING (run_id, order_id) "
                    "WHERE p.run_id = %s;", (run_id,))
        pairs = cur.fetchall()
    if not pairs:
        raise ValueError(f"No matched pairs for run_id {run_id!r}")
    actual = [float(d) for _, d, _, _, _, _ in pairs]
    predicted = [float(p) for p, _, _, _, _, _ in pairs]
    summary = {"matched": len(pairs), "overall": compute_metrics(actual, predicted)}
    storm_actual = [float(d) for _, d, payload, _, _, _ in pairs
                    if payload.get("weather_type") == "storm"]
    storm_predicted = [float(p) for p, _, payload, _, _, _ in pairs
                       if payload.get("weather_type") == "storm"]
    summary["storm"] = compute_metrics(storm_actual, storm_predicted) if storm_actual else None
    risks = [(1.0 if delivered > promised else 0.0, float(prob))
             for _, _, _, prob, delivered, promised in pairs if prob is not None]
    summary["risk_scored"] = len(risks)
    findings = []
    if summary["overall"]["mae"] >= ALERT_MAE_MINUTES:
        findings.append({"dimension": "performance", "check": "overall_mae",
                         "mae": summary["overall"]["mae"]})
    if summary["storm"] is not None and summary["storm"]["mean_bias"] <= ALERT_STORM_BIAS_MINUTES:
        findings.append({"dimension": "performance", "check": "storm_bias",
                         "mean_bias": summary["storm"]["mean_bias"]})
    return summary, findings


def main() -> None:
    parser = argparse.ArgumentParser(description="Check one logged run for drift and degradation.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--baseline", type=Path, required=True, help="Baseline JSON from build-baseline.")
    parser.add_argument("--build-baseline", type=Path, default=None,
                        help="Write baseline stats from this source file and exit.")
    args = parser.parse_args()
    if args.build_baseline is not None:
        baseline = summarize_source_file(args.build_baseline)
        args.baseline.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote baseline over {baseline['source_rows']:,} rows: {args.baseline}")
        return
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    config = db_config_from_env()
    if config is None:
        parser.exit(1, "error: POSTGRES_DB/APP_USER/APP_PASSWORD must be set\n")
    with psycopg.connect(**config) as conn:
        current = summarize_logged_run(conn, args.run_id)
        performance, perf_findings = check_performance(conn, args.run_id)
    report = {"run_id": args.run_id, "logged_predictions": current["n"],
              "performance": performance,
              "drift_findings": check_drift(baseline, current),
              "performance_findings": perf_findings,
              "alert": bool(check_drift(baseline, current) or perf_findings),
              "simulated": True}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
