import argparse
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import psycopg
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse

from models.predict_eta import validate_request
from models.refit_eta import load_artifact
from models.refit_risk import load_risk_artifact
from monitoring.checks import check_drift, check_performance, summarize_logged_run
from persistence.db import db_config_from_env
from persistence.predictions import PredictionConflict, insert_prediction
from prep.dataset_validation import parse_timestamp
from serving.dashboard import render_dashboard, run_panel

RUN_ID_HEADER = "x-run-id"
PREDICTED_AT_HEADER = "x-predicted-at"
BASELINE_PATH = Path(__file__).resolve().parents[2] / "monitoring" / "baseline_jan_aug.json"


def log_attempt(config, *, http_status: int, category: str, detail: str,
                latency_ms: float, run_id: str | None = None,
                order_id: str | None = None) -> None:
    """Best-effort operational record for a non-200 outcome on /predict.

    Only our own error message is stored, never the request body. A failing
    attempt write is swallowed on purpose: this log must never mask the
    response it describes. Callers pass the run/order correlation only when
    it parsed cleanly.
    """
    if config is None:
        return
    try:
        with psycopg.connect(**config) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO app.attempts
                       (attempt_id, run_id, order_id, http_status, category, detail,
                        attempt_latency_ms)
                       VALUES (%s, %s, %s, %s, %s, %s, %s);""",
                    (uuid.uuid4().hex, run_id, order_id, http_status, category,
                     detail, latency_ms))
    except Exception:
        pass


def create_app(artifact_dir: Path | str, risk_artifact_dir: Path | str | None = None) -> FastAPI:
    """Create a local API; explicitly selected trusted models load at startup.

    The risk artifact is optional: without it /predict serves ETA alone,
    exactly as before. With it, every response also carries late_probability.
    """
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Let artifact errors abort startup rather than serve with a fallback model.
        app.state.artifact = load_artifact(artifact_dir)
        app.state.risk = load_risk_artifact(risk_artifact_dir) if risk_artifact_dir is not None else None
        try:
            app.state.db_config = db_config_from_env()
        except ValueError as error:
            raise RuntimeError(f"Invalid database configuration: {error}") from error
        if app.state.db_config is not None:
            # Fail startup on an unreachable store; never serve half-logged.
            try:
                with psycopg.connect(**app.state.db_config) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1;")
            except Exception as error:
                config = app.state.db_config
                raise RuntimeError(
                    "Prediction store unreachable at "
                    f"{config['host']}:{config['port']}/{config['dbname']} "
                    f"as {config['user']}") from error
        try:
            yield
        finally:
            app.state.artifact = None
            app.state.risk = None

    app = FastAPI(title="Synthetic ETA API", lifespan=lifespan)
    app.state.artifact = None
    app.state.risk = None
    app.state.db_config = None
    try:
        app.state.baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        app.state.baseline = None  # Dashboard degrades to performance-only.

    def loaded_artifact():
        if app.state.artifact is None:
            raise HTTPException(status_code=503, detail="Model is not loaded")
        return app.state.artifact

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request: Request, error: RequestValidationError):
        # Do not echo arbitrary input, including NaN values that cannot be JSON responses.
        # The route never started, so this attempt carries a zero latency scope.
        log_attempt(request.app.state.db_config, http_status=422, category="invalid_request",
                    detail="Body must be a valid JSON object", latency_ms=0.0,
                    run_id=request.headers.get(RUN_ID_HEADER) or None)
        return JSONResponse(status_code=422, content={"detail": "Body must be a valid JSON object"})

    @app.get("/health")
    def health() -> dict:
        _, metadata = loaded_artifact()
        body = {"status": "ready", "model_sha256": metadata["model_sha256"],
                "simulated": metadata["simulated"]}
        if app.state.risk is not None:
            body["risk_model_sha256"] = app.state.risk[1]["model_sha256"]
        return body

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> str:
        config = app.state.db_config
        if config is None:
            raise HTTPException(status_code=503, detail="Dashboard needs a configured database")
        try:
            with psycopg.connect(**config) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT run_id FROM app.runs ORDER BY run_id;")
                    run_ids = [row[0] for row in cur.fetchall()]
                panels = []
                for run_id in run_ids:
                    try:
                        performance, perf_findings = check_performance(conn, run_id)
                    except ValueError:
                        continue  # Run logged nothing scoreable yet; skip, don't fail the page.
                    baseline = app.state.baseline
                    drift = [] if baseline is None else check_drift(
                        baseline, summarize_logged_run(conn, run_id))
                    panels.append(run_panel(run_id, performance, drift, perf_findings))
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(status_code=503, detail="Dashboard store unavailable") from error
        return render_dashboard(panels, baseline_missing=app.state.baseline is None)

    @app.post("/predict")
    def predict(request: Request, payload: Annotated[dict, Body(description="Same confirmation-time fields as the local CLI.")]) -> dict:
        entered = time.perf_counter()
        config = request.app.state.db_config
        headers_run_id = request.headers.get(RUN_ID_HEADER) or None
        predicted_at_raw = request.headers.get(PREDICTED_AT_HEADER) or None
        order_id = payload.get("order_id") if isinstance(payload, dict) else None
        if not isinstance(order_id, str) or not order_id:
            order_id = None

        def fail(http_status: int, category: str, detail: str) -> None:
            log_attempt(config, http_status=http_status, category=category, detail=detail,
                        latency_ms=(time.perf_counter() - entered) * 1000,
                        run_id=headers_run_id, order_id=order_id)
            raise HTTPException(status_code=http_status, detail=detail)

        model, metadata = loaded_artifact()
        risk = request.app.state.risk
        try:
            features = validate_request(payload)
        except ValueError as error:
            fail(422, "invalid_request", str(error))
        # Run context travels in headers so the body stays model features only.
        if (headers_run_id is None) != (predicted_at_raw is None):
            fail(422, "invalid_request", "X-Run-Id and X-Predicted-At must be sent together")
        run_id = headers_run_id
        # A synchronous route keeps CPU-bound model prediction off the async event loop.
        start = time.perf_counter()
        try:
            duration = model.predict([features])[0]
            probability = risk[0].predict([features])[0] if risk is not None else None
        except Exception:
            fail(500, "internal_error", "Model inference failed")
        latency_ms = (time.perf_counter() - start) * 1000
        if run_id is None:
            body = {"order_id": payload["order_id"], "predicted_delivery_duration_minutes": duration,
                    "model_sha256": metadata["model_sha256"], "simulated": metadata["simulated"]}
            if risk is not None:
                body["late_probability"] = probability
                body["risk_model_sha256"] = risk[1]["model_sha256"]
            return body
        try:
            predicted_at = parse_timestamp(predicted_at_raw)
        except ValueError:
            fail(422, "invalid_request", "X-Predicted-At must be a timezone-aware ISO timestamp")
        if config is None:
            fail(503, "store_unavailable", "Prediction logging requested but no database is configured")
        try:
            # The context manager commits before we respond; a lost response
            # is then safe to retry. Failures roll back instead.
            with psycopg.connect(**config) as conn:
                row = insert_prediction(
                    conn, run_id=run_id, order_id=payload["order_id"],
                    request_payload=payload, features=features,
                    predicted_delivery_duration_minutes=duration,
                    model_sha256=metadata["model_sha256"],
                    predicted_at_simulated=predicted_at, model_latency_ms=latency_ms,
                    late_probability=probability)
        except PredictionConflict as error:
            fail(409, "conflict", str(error))
        except psycopg.errors.ForeignKeyViolation as error:
            fail(422, "invalid_request", f"Unknown run_id {run_id!r}; register the run first")
        except psycopg.OperationalError:
            fail(503, "store_unavailable", "Prediction store unavailable")
        except psycopg.Error:
            fail(500, "internal_error", "Prediction store write failed")
        # Respond from the stored row so retries return the identical prediction.
        # Conflict comparison already proved stored equals fresh, except for
        # legacy NULL rows, which fall back to the fresh probability.
        body = {"order_id": row["order_id"],
                "predicted_delivery_duration_minutes": row["predicted_delivery_duration_minutes"],
                "model_sha256": row["model_sha256"], "simulated": row["simulated"]}
        if risk is not None:
            stored_probability = row["late_probability"]
            body["late_probability"] = probability if stored_probability is None else float(stored_probability)
            body["risk_model_sha256"] = risk[1]["model_sha256"]
        return body

    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve the synthetic ETA model (localhost by default)."
    )
    parser.add_argument(
        "--model-dir", type=Path, required=True,
        help="Our own trusted artifact directory."
    )
    parser.add_argument(
        "--risk-model-dir", type=Path, default=None,
        help="Optional trusted risk artifact directory; without it /predict serves ETA alone."
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Bind address; use 0.0.0.0 inside Docker."
    )
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    uvicorn.run(
        create_app(args.model_dir, args.risk_model_dir),
        host=args.host,
        port=args.port,
        workers=1,
    )


if __name__ == "__main__":
    main()
