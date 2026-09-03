import argparse
from pathlib import Path
from typing import Any

import numpy as np
from lightgbm import LGBMRegressor, early_stopping

from models.baselines import (
    GlobalMeanBaseline,
    HeuristicBaseline,
    LinearRegressionBaseline,
    compute_metrics,
    compute_segment_metrics,
    print_segment_metrics,
)
from prep.dataset_validation import prepare_dataset

NUMERICAL_FEATURES = (
    "distance_km",
    "item_count",
    "traffic_index",
    "restaurant_backlog",
    "orders_waiting_for_courier",
    "idle_couriers",
    "precipitation_mm_per_hour",
    "temperature_c",
    "local_hour",
    "day_of_week",
)

CATEGORICAL_FEATURES = (
    "pickup_zone_id",
    "dropoff_zone_id",
    "weather_type",
)

ALL_FEATURES = NUMERICAL_FEATURES + CATEGORICAL_FEATURES

ZONE_MAP = {"Z1": 0, "Z2": 1, "Z3": 2}
WEATHER_MAP = {"clear": 0, "rain": 1, "snow": 2, "storm": 3}
UNKNOWN_CATEGORY_CODE = -1
PREDICTION_FLOOR_MINUTES = 5.0
PREDICTION_DECIMALS = 2


def extract_features(orders: list[dict]) -> np.ndarray:
    """Transform order dictionaries into a 2D feature matrix."""
    rows: list[list[float]] = []
    for o in orders:
        num_vals = [float(o[f]) for f in NUMERICAL_FEATURES]
        # Encode categoricals as integers; fallback to -1 for unseen categories
        cat_vals = [
            float(ZONE_MAP.get(o["pickup_zone_id"], UNKNOWN_CATEGORY_CODE)),
            float(ZONE_MAP.get(o["dropoff_zone_id"], UNKNOWN_CATEGORY_CODE)),
            float(WEATHER_MAP.get(o["weather_type"], UNKNOWN_CATEGORY_CODE)),
        ]
        rows.append(num_vals + cat_vals)
    return np.array(rows, dtype=np.float64)


def extract_target(orders: list[dict]) -> np.ndarray:
    """Extract delivery duration target vector."""
    return np.array([float(o["delivery_duration_minutes"]) for o in orders], dtype=np.float64)


class LightGBMETAModel:
    """Gradient boosted decision tree model optimizing MAE (L1 loss)."""

    def __init__(
        self,
        n_estimators: int = 300,
        learning_rate: float = 0.05,
        num_leaves: int = 31,
        min_child_samples: int = 20,
        random_state: int = 42,
        early_stopping_rounds: int = 25,
    ) -> None:
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.min_child_samples = min_child_samples
        self.random_state = random_state
        self.early_stopping_rounds = early_stopping_rounds

        self.model: LGBMRegressor | None = None
        self.is_fitted = False
        self.cat_indices = [ALL_FEATURES.index(f) for f in CATEGORICAL_FEATURES]

    def fit(
        self,
        train_orders: list[dict],
        val_orders: list[dict] | None = None,
        verbose: bool = False,
    ) -> "LightGBMETAModel":
        if not train_orders:
            raise ValueError("Cannot fit LightGBMETAModel on empty training data")

        X_train = extract_features(train_orders)
        y_train = extract_target(train_orders)

        self.model = LGBMRegressor(
            objective="regression_l1",
            metric="l1",
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            num_leaves=self.num_leaves,
            min_child_samples=self.min_child_samples,
            random_state=self.random_state,
            n_jobs=-1,
            verbosity=-1,
        )

        callbacks: list[Any] = []
        X_val = y_val = None

        if val_orders:
            X_val = extract_features(val_orders)
            y_val = extract_target(val_orders)
            callbacks.append(early_stopping(stopping_rounds=self.early_stopping_rounds, verbose=verbose))

        self.model.fit(
            X_train,
            y_train,
            eval_X=X_val,
            eval_y=y_val,
            categorical_feature=self.cat_indices,
            callbacks=callbacks,
        )
        self.is_fitted = True
        return self

    def predict(self, orders: list[dict]) -> list[float]:
        if not self.is_fitted or self.model is None:
            raise ValueError("Model must be fit before calling predict")
        if not orders:
            return []

        X = extract_features(orders)
        raw_preds = self.model.predict(X)
        # Floor predictions at 5.0 minutes to prevent physically impossible estimates
        return [round(float(max(PREDICTION_FLOOR_MINUTES, p)), PREDICTION_DECIMALS) for p in raw_preds]

    def get_feature_importances(self, importance_type: str = "gain") -> dict[str, float]:
        """Return feature names mapped to their importance values."""
        if not self.is_fitted or self.model is None:
            raise ValueError("Model must be fit before retrieving feature importances")
        importances = self.model.booster_.feature_importance(importance_type=importance_type)
        return dict(
            sorted(
                zip(ALL_FEATURES, (float(v) for v in importances)),
                key=lambda item: item[1],
                reverse=True,
            )
        )


def evaluate_eta_models(
    splits: dict[str, list[dict]], *, include_test: bool = False,
) -> tuple[dict[str, dict[str, dict]], LightGBMETAModel, LinearRegressionBaseline]:
    """Compare frozen models; validation selects tree count, never the test set."""
    train_orders = splits["train"]
    val_orders = splits["val"]

    # 1. Global Mean Baseline
    mean_model = GlobalMeanBaseline().fit(train_orders)

    # 2. Linear Regression Baseline
    lr_model = LinearRegressionBaseline().fit(train_orders)

    # 3. LightGBM ETA Model
    lgb_model = LightGBMETAModel().fit(train_orders, val_orders=val_orders)

    models = {
        "Global Mean": mean_model,
        "Domain Heuristic": HeuristicBaseline().fit(train_orders),
        "Linear Regression": lr_model,
        "LightGBM": lgb_model,
    }

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

    return results, lgb_model, lr_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and evaluate LightGBM ETA model against baselines.")
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

    print("Synthetic data only. Test scoring is " + ("enabled." if args.include_test else "disabled."))
    results, lgb_model, _ = evaluate_eta_models(splits, include_test=args.include_test)

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

    best_iter = getattr(lgb_model.model, "best_iteration_", None)
    if best_iter:
        print(f"\nLightGBM validation-selected iteration: {best_iter} / {lgb_model.n_estimators}")

    print("\n=== LightGBM Feature Importances (Total Gain) ===")
    gain_importances = lgb_model.get_feature_importances("gain")
    max_gain = max(gain_importances.values(), default=0.0) or 1.0
    for feat, gain in gain_importances.items():
        pct = (gain / max_gain) * 100
        print(f"  {feat:<28}: {gain:>12.1f} ({pct:>5.1f}% of top)")


if __name__ == "__main__":
    main()
