import argparse
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import psycopg
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from models.predict_eta import validate_request
from models.refit_eta import load_artifact
from persistence.db import db_config_from_env
from persistence.predictions import PredictionConflict, insert_prediction
from prep.dataset_validation import parse_timestamp

RUN_ID_HEADER = "x-run-id"
PREDICTED_AT_HEADER = "x-predicted-at"


def create_app(artifact_dir: Path | str) -> FastAPI:
    """Create a local API; the explicitly selected trusted model loads at startup."""
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Let artifact errors abort startup rather than serve with a fallback model.
        app.state.artifact = load_artifact(artifact_dir)
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

    app = FastAPI(title="Synthetic ETA API", lifespan=lifespan)
    app.state.artifact = None
    app.state.db_config = None

    def loaded_artifact():
        if app.state.artifact is None:
            raise HTTPException(status_code=503, detail="Model is not loaded")
        return app.state.artifact

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request: Request, error: RequestValidationError):
        # Do not echo arbitrary input, including NaN values that cannot be JSON responses.
        return JSONResponse(status_code=422, content={"detail": "Body must be a valid JSON object"})

    @app.get("/health")
    def health() -> dict:
        _, metadata = loaded_artifact()
        return {"status": "ready", "model_sha256": metadata["model_sha256"],
                "simulated": metadata["simulated"]}

    @app.post("/predict")
    def predict(request: Request, payload: Annotated[dict, Body(description="Same confirmation-time fields as the local CLI.")]) -> dict:
        model, metadata = loaded_artifact()
        try:
            features = validate_request(payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        # Run context travels in headers so the body stays model features only.
        run_id = request.headers.get(RUN_ID_HEADER) or None
        predicted_at_raw = request.headers.get(PREDICTED_AT_HEADER) or None
        if (run_id is None) != (predicted_at_raw is None):
            raise HTTPException(status_code=422,
                                detail="X-Run-Id and X-Predicted-At must be sent together")
        # A synchronous route keeps CPU-bound model prediction off the async event loop.
        start = time.perf_counter()
        duration = model.predict([features])[0]
        latency_ms = (time.perf_counter() - start) * 1000
        if run_id is None:
            return {"order_id": payload["order_id"], "predicted_delivery_duration_minutes": duration,
                    "model_sha256": metadata["model_sha256"], "simulated": metadata["simulated"]}
        try:
            predicted_at = parse_timestamp(predicted_at_raw)
        except ValueError as error:
            raise HTTPException(status_code=422,
                                detail="X-Predicted-At must be a timezone-aware ISO timestamp") from error
        config = request.app.state.db_config
        if config is None:
            raise HTTPException(status_code=503,
                                detail="Prediction logging requested but no database is configured")
        try:
            # The context manager commits before we respond; a lost response
            # is then safe to retry. Failures roll back instead.
            with psycopg.connect(**config) as conn:
                row = insert_prediction(
                    conn, run_id=run_id, order_id=payload["order_id"],
                    request_payload=payload, features=features,
                    predicted_delivery_duration_minutes=duration,
                    model_sha256=metadata["model_sha256"],
                    predicted_at_simulated=predicted_at, model_latency_ms=latency_ms)
        except PredictionConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except psycopg.errors.ForeignKeyViolation as error:
            raise HTTPException(status_code=422,
                                detail=f"Unknown run_id {run_id!r}; register the run first") from error
        except psycopg.OperationalError as error:
            raise HTTPException(status_code=503, detail="Prediction store unavailable") from error
        # Respond from the stored row so retries return the identical prediction.
        return {"order_id": row["order_id"],
                "predicted_delivery_duration_minutes": row["predicted_delivery_duration_minutes"],
                "model_sha256": row["model_sha256"], "simulated": row["simulated"]}

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
        "--host", default="127.0.0.1",
        help="Bind address; use 0.0.0.0 inside Docker."
    )
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    uvicorn.run(
        create_app(args.model_dir),
        host=args.host,
        port=args.port,
        workers=1,
    )


if __name__ == "__main__":
    main()
