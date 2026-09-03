import argparse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from models.predict_eta import validate_request
from models.refit_eta import load_artifact


def create_app(artifact_dir: Path | str) -> FastAPI:
    """Create a local API; the explicitly selected trusted model loads at startup."""
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Let artifact errors abort startup rather than serve with a fallback model.
        app.state.artifact = load_artifact(artifact_dir)
        try:
            yield
        finally:
            app.state.artifact = None

    app = FastAPI(title="Synthetic ETA API", lifespan=lifespan)
    app.state.artifact = None

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
    def predict(payload: Annotated[dict, Body(description="Same confirmation-time fields as the local CLI.")]) -> dict:
        model, metadata = loaded_artifact()
        try:
            features = validate_request(payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        # A synchronous route keeps CPU-bound model prediction off the async event loop.
        duration = model.predict([features])[0]
        return {"order_id": payload["order_id"], "predicted_delivery_duration_minutes": duration,
                "model_sha256": metadata["model_sha256"], "simulated": metadata["simulated"]}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the synthetic ETA model on localhost only.")
    parser.add_argument("--model-dir", type=Path, required=True, help="Our own trusted artifact directory.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    uvicorn.run(create_app(args.model_dir), host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
