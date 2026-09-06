import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import joblib

from models import late_risk as risk
from models.late_risk import LightGBMRiskModel
from models.lightgbm_eta import ALL_FEATURES, CATEGORICAL_FEATURES, NUMERICAL_FEATURES, WEATHER_MAP, ZONE_MAP
from prep.dataset_validation import load_orders, parse_timestamp, separate_cancellations

# Frozen by July validation (log-loss 0.2557), not selected again here.
REFIT_TREES = 160


def file_sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def runtime_versions() -> dict[str, str]:
    packages = ("numpy", "scikit-learn", "lightgbm", "scipy", "joblib",
                "threadpoolctl", "narwhals", "cloudpickle")
    return {"python": platform.python_version(), **{name: version(name) for name in packages}}


def feature_contract() -> dict:
    return {
        "target": "late_delivery",
        "prediction": "probability in [0, 1]",
        "prediction_moment": "order_confirmation",
        "feature_order": list(ALL_FEATURES),
        "numerical_features": list(NUMERICAL_FEATURES),
        "categorical_features": list(CATEGORICAL_FEATURES),
        "zone_map": dict(ZONE_MAP),
        "weather_map": dict(WEATHER_MAP),
        "matrix_dtype": "float64",
    }


def load_risk_artifact(directory: Path | str) -> tuple[LightGBMRiskModel, dict]:
    """Load our own trusted artifacts only: Joblib can execute code when loading."""
    directory = Path(directory)
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if metadata["format_version"] != 1 or metadata["feature_contract"] != feature_contract():
        raise ValueError("Artifact feature contract is incompatible with this code")
    if metadata["runtime_versions"] != runtime_versions():
        raise ValueError("Artifact requires the recorded Python/package versions")
    model_path = directory / "model.joblib"
    if metadata["model_sha256"] != file_sha256(model_path):
        raise ValueError("Artifact model checksum mismatch")
    model = joblib.load(model_path)
    if not isinstance(model, LightGBMRiskModel) or not model.is_fitted:
        raise ValueError("Artifact does not contain a fitted risk model")
    if model.model.booster_.num_trees() != metadata["tree_count"]:
        raise ValueError("Artifact tree count disagrees with metadata")
    return model, metadata


def refit_risk(data: Path | str, output_dir: Path | str, *, observed_at: datetime) -> dict:
    """Refit all delivered rows after their labels arrive; save and verify probabilities."""
    data, output_dir = Path(data), Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite artifact directory: {output_dir}")
    if observed_at.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    observed_at = observed_at.astimezone(timezone.utc)
    data_hash = file_sha256(data)
    orders = load_orders(data)
    delivered, cancelled = separate_cancellations(orders)
    if not delivered:
        raise ValueError("No delivered orders available for refitting")
    if any(not isinstance(o.get("late_delivery"), bool) for o in delivered):
        raise ValueError("Every delivered order needs a boolean late_delivery label")
    confirmations = [parse_timestamp(order["confirmed_at"]) for order in orders]
    training_confirmations = [parse_timestamp(order["confirmed_at"]) for order in delivered]
    latest_delivery = max(parse_timestamp(order["delivered_at"]) for order in delivered)
    if max(confirmations) >= observed_at or latest_delivery >= observed_at:
        raise ValueError("Full-data refit requires all confirmations and deliveries before observed_at")
    if data_hash != file_sha256(data):
        raise ValueError("Source dataset changed while it was being loaded")

    model = LightGBMRiskModel(n_estimators=REFIT_TREES).fit(delivered)
    tree_count = model.model.booster_.num_trees()
    if tree_count != REFIT_TREES:
        raise ValueError(f"Expected {REFIT_TREES} trees, fitted {tree_count}; check training data")

    output_dir.mkdir(parents=True, exist_ok=False)
    model_path = output_dir / "model.joblib"
    joblib.dump(model, model_path)
    metadata = {
        "format_version": 1,
        "simulated": True,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "observed_at": observed_at.isoformat(),
        "source_file": data.name,
        "source_sha256": data_hash,
        "total_orders": len(orders),
        "training_rows": len(delivered),
        "cancelled_rows_excluded": len(cancelled),
        "source_confirmation_window_utc": [min(confirmations).isoformat(), max(confirmations).isoformat()],
        "training_confirmation_window_utc": [min(training_confirmations).isoformat(), max(training_confirmations).isoformat()],
        "latest_delivery_utc": latest_delivery.isoformat(),
        "tree_count": tree_count,
        "tree_selection_record": "July validation (log-loss 0.2557; see docs/handoff.md)",
        "early_stopping_used": False,
        "feature_contract": feature_contract(),
        "model_parameters": model.model.get_params(),
        "runtime_versions": runtime_versions(),
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "prediction_code_sha256": file_sha256(Path(risk.__file__)),
        "refit_code_sha256": file_sha256(Path(__file__)),
        "model_sha256": file_sha256(model_path),
        "evaluation_note": "January-August is now training data; no held-out score for this refit.",
    }
    # Check the just-written trusted model before publishing its metadata sidecar.
    reloaded = joblib.load(model_path)
    predictions = model.predict(delivered)
    if predictions != reloaded.predict(delivered):
        raise AssertionError("Reloaded model predictions differ from the fitted model")
    metadata["roundtrip_verified_rows"] = len(delivered)
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    loaded, _ = load_risk_artifact(output_dir)
    if predictions != loaded.predict(delivered):
        raise AssertionError("Public artifact loader changed predictions")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Refit the frozen synthetic risk model and save it locally.")
    parser.add_argument("--data", type=Path, default=Path("data/orders_2026_jan_aug.json"))
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory; existing artifacts are never overwritten.")
    parser.add_argument("--observed-at", type=parse_timestamp, required=True, help="Timezone-aware label observation time.")
    args = parser.parse_args()
    metadata = refit_risk(args.data, args.output_dir, observed_at=args.observed_at)
    print(f"Saved synthetic risk model to {args.output_dir}: {metadata['training_rows']:,} delivered orders, "
          f"{metadata['tree_count']} trees; exact reload predictions verified on all training rows.")
    print("No held-out performance is claimed for this full-data refit.")


if __name__ == "__main__":
    main()
