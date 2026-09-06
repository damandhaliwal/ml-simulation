"""Minimal read-only dashboard: run scores and alert flags as plain HTML."""

import html

FAILED_BASELINE_NOTE = "input-drift baseline unavailable"


def render_dashboard(panels: list[dict], baseline_missing: bool = False) -> str:
    """Render one card per replay run; all values are precomputed, never raw bodies."""
    cards = []
    for panel in panels:
        rows = "".join(
            f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>"
            for k, v in panel["metrics"].items())
        findings = "".join(
            f"<li>{html.escape(str(f))}</li>" for f in panel["findings"])
        findings = findings or "<li>none</li>"
        cards.append(f"<section><h2>{html.escape(panel['run_id'])}</h2>"
                     f"<table>{rows}</table><h3>findings</h3><ul>{findings}</ul></section>")
    drift_note = f"<p>{FAILED_BASELINE_NOTE}</p>" if baseline_missing else ""
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>ETA replay dashboard (simulated)</title></head>"
            "<body><h1>ETA replay dashboard (simulated)</h1>"
            f"{drift_note}{''.join(cards) or '<p>no logged runs</p>'}</body></html>")


def run_panel(run_id: str, performance: dict, drift_findings: list[dict],
              performance_findings: list[dict]) -> dict:
    """Flatten one run's monitor output into dashboard metrics."""
    overall = performance["overall"]
    storm = performance.get("storm") or {}
    return {
        "run_id": run_id,
        "metrics": {
            "matched_deliveries": performance["matched"],
            "mae_minutes": overall["mae"],
            "bias_minutes": overall["mean_bias"],
            "p95_minutes": overall["p95_error"],
            "storm_bias_minutes": storm.get("mean_bias", "n/a"),
            "alert": bool(drift_findings or performance_findings),
        },
        "findings": drift_findings + performance_findings,
    }
