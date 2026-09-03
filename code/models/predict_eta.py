import argparse
import json
from math import isfinite
from pathlib import Path

from models.lightgbm_eta import ALL_FEATURES, NUMERICAL_FEATURES, WEATHER_MAP, ZONE_MAP
from models.refit_eta import load_artifact
from prep.dataset_validation import MARKET_TIMEZONE, parse_timestamp

DERIVED_FEATURES = ("local_hour", "day_of_week")
REQUEST_FIELDS = ("order_id", "confirmed_at") + tuple(
    field for field in ALL_FEATURES if field not in DERIVED_FEATURES
)
COUNT_MINIMUMS = {
    "item_count": 1,
    "restaurant_backlog": 0,
    "orders_waiting_for_courier": 0,
    "idle_couriers": 0,
}


def validate_request(request: dict) -> dict:
    """Validate one confirmation-time request and return the model's feature row."""
    if not isinstance(request, dict) or any(not isinstance(key, str) for key in request):
        raise ValueError("Request must be a JSON object with string keys")
    missing = set(REQUEST_FIELDS) - request.keys()
    extra = request.keys() - set(REQUEST_FIELDS)
    if missing:
        raise ValueError(f"Missing fields: {', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"Unexpected fields: {', '.join(sorted(extra))}")
    if not isinstance(request["order_id"], str) or not request["order_id"].strip():
        raise ValueError("order_id must be a nonempty string")
    try:
        local = parse_timestamp(request["confirmed_at"]).astimezone(MARKET_TIMEZONE)
    except (ValueError, OverflowError) as error:
        raise ValueError("confirmed_at must be a timezone-aware ISO timestamp convertible to Toronto time") from error

    features = {field: request[field] for field in ALL_FEATURES if field not in DERIVED_FEATURES}
    for field in NUMERICAL_FEATURES:
        if field in DERIVED_FEATURES:
            continue
        value = features[field]
        if type(value) not in (int, float):
            raise ValueError(f"{field} must be a finite number, not a string or boolean")
        try:
            finite = isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError(f"{field} must be a finite number representable as float64")
        if field in COUNT_MINIMUMS:
            minimum = COUNT_MINIMUMS[field]
            if type(value) is not int or value < minimum:
                raise ValueError(f"{field} must be an integer >= {minimum}")
    for field in ("distance_km", "traffic_index"):
        if features[field] <= 0:
            raise ValueError(f"{field} must be positive")
    if features["precipitation_mm_per_hour"] < 0:
        raise ValueError("precipitation_mm_per_hour must be nonnegative")
    for field, allowed in (("pickup_zone_id", ZONE_MAP), ("dropoff_zone_id", ZONE_MAP),
                           ("weather_type", WEATHER_MAP)):
        if not isinstance(features[field], str) or features[field] not in allowed:
            raise ValueError(f"{field} must be one of {', '.join(allowed)}")

    # Match the simulator's weather rules; these are toy-domain constraints.
    weather = features["weather_type"]
    if (weather == "clear") != (features["precipitation_mm_per_hour"] == 0):
        raise ValueError("precipitation_mm_per_hour must be zero for clear weather and positive otherwise")
    temperature = features["temperature_c"]
    if weather == "snow" and temperature > 0:
        raise ValueError("temperature_c must be <= 0 for snow")
    if weather in ("rain", "storm") and temperature <= 0:
        raise ValueError("temperature_c must be > 0 for rain/storm")

    features.update(local_hour=local.hour, day_of_week=local.weekday())
    return features


def predict_eta(request: dict, artifact_dir: Path | str) -> dict:
    """Validate and predict one order using an explicitly selected trusted artifact."""
    features = validate_request(request)
    model, metadata = load_artifact(artifact_dir)
    return {
        "order_id": request["order_id"],
        "predicted_delivery_duration_minutes": model.predict([features])[0],
        "model_sha256": metadata["model_sha256"],
        "simulated": metadata["simulated"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict one synthetic order's ETA locally; no training or API.")
    parser.add_argument("--model-dir", type=Path, required=True, help="Our own trusted model artifact directory.")
    parser.add_argument("--input", type=Path, required=True, help="JSON file containing one confirmation-time request.")
    args = parser.parse_args()
    try:
        request = json.loads(args.input.read_text(encoding="utf-8"))
        result = predict_eta(request, args.model_dir)
    except (OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
