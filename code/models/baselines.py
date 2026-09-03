import argparse
from math import isfinite, sqrt
from pathlib import Path
from statistics import mean
from typing import Protocol

import numpy as np
from sklearn.linear_model import LinearRegression

from prep.dataset_validation import prepare_dataset

INFERENCE_FEATURES = (
    "distance_km",
    "item_count",
    "traffic_index",
    "restaurant_backlog",
    "orders_waiting_for_courier",
    "idle_couriers",
    "precipitation_mm_per_hour",
)


class BaselineModel(Protocol):
    def fit(self, orders: list[dict]) -> "BaselineModel":
        ...

    def predict(self, orders: list[dict]) -> list[float]:
        ...


class GlobalMeanBaseline:
    """Predicts the constant mean delivery duration from the training set."""

    def __init__(self) -> None:
        self.mean_duration: float | None = None

    def fit(self, orders: list[dict]) -> "GlobalMeanBaseline":
        if not orders:
            raise ValueError("Cannot fit GlobalMeanBaseline on empty orders")
        self.mean_duration = float(mean(o["delivery_duration_minutes"] for o in orders))
        return self

    def predict(self, orders: list[dict]) -> list[float]:
        if self.mean_duration is None:
            raise ValueError("Model must be fit before calling predict")
        return [self.mean_duration] * len(orders)


class HeuristicBaseline:
    """Domain rule: fixed prep time + item handling + traffic-adjusted travel speed."""

    def __init__(self, base_prep_min: float = 15.0, per_item_min: float = 2.0, min_per_km: float = 4.0) -> None:
        self.base_prep_min = base_prep_min
        self.per_item_min = per_item_min
        self.min_per_km = min_per_km

    def fit(self, orders: list[dict]) -> "HeuristicBaseline":
        # Heuristic requires no parameter fitting from data.
        return self

    def predict(self, orders: list[dict]) -> list[float]:
        preds: list[float] = []
        for o in orders:
            prep = self.base_prep_min + self.per_item_min * o["item_count"]
            travel = self.min_per_km * o["distance_km"] * o["traffic_index"]
            preds.append(round(max(5.0, prep + travel), 2))
        return preds


class LinearRegressionBaseline:
    """Least-squares regression on features available at order confirmation."""

    def __init__(self, feature_names: tuple[str, ...] = INFERENCE_FEATURES) -> None:
        self.feature_names = feature_names
        self.model = LinearRegression()
        self.is_fitted = False

    def _extract_matrix(self, orders: list[dict]) -> np.ndarray:
        return np.array(
            [[float(o[f]) for f in self.feature_names] for o in orders],
            dtype=np.float64,
        )

    def fit(self, orders: list[dict]) -> "LinearRegressionBaseline":
        if not orders:
            raise ValueError("Cannot fit LinearRegressionBaseline on empty orders")
        X = self._extract_matrix(orders)
        y = np.array([float(o["delivery_duration_minutes"]) for o in orders], dtype=np.float64)
        self.model.fit(X, y)
        self.is_fitted = True
        return self

    def predict(self, orders: list[dict]) -> list[float]:
        if not self.is_fitted:
            raise ValueError("Model must be fit before calling predict")
        X = self._extract_matrix(orders)
        raw_preds = self.model.predict(X)
        # Floor predictions at 5 minutes to avoid impossible physical times
        return [round(float(max(5.0, p)), 2) for p in raw_preds]


def compute_metrics(y_true: list[float], y_pred: list[float]) -> dict[str, float]:
    """Calculate MAE, Mean Bias (predicted - actual), P95 error, and RMSE."""
    if len(y_true) != len(y_pred):
        raise ValueError(f"Length mismatch: {len(y_true)} actual vs {len(y_pred)} predicted")
    if not y_true:
        raise ValueError("Cannot evaluate metrics on empty lists")
    if any(not isfinite(value) for value in (*y_true, *y_pred)):
        raise ValueError("Metrics require finite actuals and predictions")

    errors = [p - a for a, p in zip(y_true, y_pred)]
    abs_errors = [abs(e) for e in errors]

    mae = mean(abs_errors)
    mean_bias = mean(errors)
    p95_error = float(np.percentile(abs_errors, 95))
    rmse = sqrt(mean(e * e for e in errors))

    return {
        "mae": round(mae, 3),
        "mean_bias": round(mean_bias, 3),
        "p95_error": round(p95_error, 3),
        "rmse": round(rmse, 3),
    }


def compute_segment_metrics(orders: list[dict], predictions: list[float]) -> dict[str, dict]:
    """Observed confirmation-time segments; each dimension partitions the scored rows."""
    if len(orders) != len(predictions):
        raise ValueError("Orders and predictions must have the same length")
    groups: dict[str, dict[str, list[int]]] = {}
    for index, order in enumerate(orders):
        distance = order["distance_km"]
        segments = {
            "weather_type": order["weather_type"],
            "pickup_zone_id": order["pickup_zone_id"],
            "local_hour": f"{order['local_hour']:02d}",
            "distance_band": "0-2 km" if distance <= 2 else "2-4 km" if distance <= 4 else ">4 km",
            "idle_couriers": str(order["idle_couriers"]),
        }
        for dimension, value in segments.items():
            groups.setdefault(dimension, {}).setdefault(value, []).append(index)

    results = {}
    for dimension, values in groups.items():
        results[dimension] = {}
        for value, indices in sorted(values.items()):
            actuals = [float(orders[i]["delivery_duration_minutes"]) for i in indices]
            preds = [predictions[i] for i in indices]
            results[dimension][value] = {"count": len(indices), **compute_metrics(actuals, preds)}
    return results


def print_segment_metrics(results: dict[str, dict[str, dict]]) -> None:
    for model_name, split_metrics in results.items():
        for split_name, metrics in split_metrics.items():
            for dimension, groups in metrics.get("segments", {}).items():
                print(f"\n{model_name} / {split_name.upper()} / {dimension} (simulated)")
                for value, m in groups.items():
                    print(f"  {value:<12} n={m['count']:>6} MAE={m['mae']:.3f} "
                          f"bias={m['mean_bias']:+.3f} P95={m['p95_error']:.3f} RMSE={m['rmse']:.3f}")


def evaluate_baselines(
    splits: dict[str, list[dict]], *, include_test: bool = False,
) -> tuple[dict[str, dict[str, dict]], LinearRegressionBaseline]:
    """Fit on train; score train/validation and optionally the frozen test set."""
    models: dict[str, BaselineModel] = {
        "Global Mean": GlobalMeanBaseline(),
        "Domain Heuristic": HeuristicBaseline(),
        "Linear Regression": LinearRegressionBaseline(),
    }

    train_orders = splits["train"]
    for model in models.values():
        model.fit(train_orders)

    results: dict[str, dict[str, dict]] = {}
    for model_name, model in models.items():
        results[model_name] = {}
        for split_name in (("train", "val", "test") if include_test else ("train", "val")):
            orders = splits[split_name]
            y_true = [float(o["delivery_duration_minutes"]) for o in orders]
            y_pred = model.predict(orders)
            metrics = {"count": len(orders), **compute_metrics(y_true, y_pred)}
            if split_name != "train":
                metrics["segments"] = compute_segment_metrics(orders, y_pred)
            results[model_name][split_name] = metrics

    lr_model = models["Linear Regression"]
    assert isinstance(lr_model, LinearRegressionBaseline)
    return results, lr_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate baseline models on chronological splits.")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data/orders_2026_jan_aug.json"),
        help="Path to orders JSON file.",
    )
    parser.add_argument("--include-test", action="store_true", help="Explicitly score the final test set; do not tune on it.")
    parser.add_argument("--segments", action="store_true", help="Print validation/test segment metrics in minutes.")
    args = parser.parse_args()

    dataset = prepare_dataset(args.data)
    splits = dataset["splits"]
    results, lr_model = evaluate_baselines(splits, include_test=args.include_test)

    print("Synthetic data only. Test scoring is " + ("enabled." if args.include_test else "disabled."))
    print("\n" + "=" * 76)
    print(f"{'Model':<20} | {'Split':<6} | {'MAE (m)':<8} | {'Bias (m)':<9} | {'P95 (m)':<8} | {'RMSE (m)':<8}")
    print("-" * 76)
    for model_name, split_metrics in results.items():
        for split_name, m in split_metrics.items():
            print(
                f"{model_name:<20} | {split_name.upper():<6} | "
                f"{m['mae']:<8.3f} | {m['mean_bias']:<9.3f} | {m['p95_error']:<8.3f} | {m['rmse']:<8.3f}"
            )
        print("-" * 76)

    if args.segments:
        print_segment_metrics(results)

    print("\n=== Linear Regression Learned Weights ===")
    print(f"Intercept: {lr_model.model.intercept_:.3f}")
    for feature, coef in zip(lr_model.feature_names, lr_model.model.coef_):
        print(f"  {feature:<28}: {coef:+.3f}")


if __name__ == "__main__":
    main()
