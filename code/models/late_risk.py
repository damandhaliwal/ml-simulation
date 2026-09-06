import argparse
from pathlib import Path
from typing import Any

import numpy as np
from lightgbm import LGBMClassifier, early_stopping
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from models.lightgbm_eta import ALL_FEATURES, CATEGORICAL_FEATURES, LightGBMETAModel, extract_features
from prep.dataset_validation import parse_timestamp, prepare_dataset

# Fixed guard so rule-based probabilities never make log-loss infinite.
PROBABILITY_EPSILON = 0.05


def extract_risk_target(orders: list[dict]) -> np.ndarray:
    """Extract the late-delivery label vector (1.0 late, 0.0 on time)."""
    return np.array([1.0 if o["late_delivery"] else 0.0 for o in orders], dtype=np.float64)


def promise_minutes(order: dict) -> float:
    """Per-order promise length from its own timestamps, never an assumed 45."""
    confirmed = parse_timestamp(order["confirmed_at"])
    promised = parse_timestamp(order["promised_delivery_at"])
    return (promised - confirmed).total_seconds() / 60


class ConstantRiskBaseline:
    """Predicts the training late rate for every order."""

    def __init__(self) -> None:
        self.base_rate: float | None = None

    def fit(self, orders: list[dict]) -> "ConstantRiskBaseline":
        if not orders:
            raise ValueError("Cannot fit ConstantRiskBaseline on empty orders")
        self.base_rate = float(extract_risk_target(orders).mean())
        return self

    def predict(self, orders: list[dict]) -> list[float]:
        if self.base_rate is None:
            raise ValueError("Model must be fit before calling predict")
        return [self.base_rate] * len(orders)


class ETAThresholdBaseline:
    """High risk when the frozen ETA exceeds the order's own promise, else low."""

    def __init__(self, eta_model: LightGBMETAModel,
                 high: float = 1.0 - PROBABILITY_EPSILON,
                 low: float = PROBABILITY_EPSILON) -> None:
        self.eta_model = eta_model
        self.high = high
        self.low = low

    def fit(self, orders: list[dict]) -> "ETAThresholdBaseline":
        return self

    def predict(self, orders: list[dict]) -> list[float]:
        etas = self.eta_model.predict(orders)
        return [self.high if eta > promise_minutes(o) else self.low
                for eta, o in zip(etas, orders)]


class LightGBMRiskModel:
    """Gradient boosted classifier for P(late delivery)."""

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

        self.model: LGBMClassifier | None = None
        self.is_fitted = False
        self.cat_indices = [ALL_FEATURES.index(f) for f in CATEGORICAL_FEATURES]

    def fit(
        self,
        train_orders: list[dict],
        val_orders: list[dict] | None = None,
        verbose: bool = False,
    ) -> "LightGBMRiskModel":
        if not train_orders:
            raise ValueError("Cannot fit LightGBMRiskModel on empty training data")

        X_train = extract_features(train_orders)
        y_train = extract_risk_target(train_orders)

        self.model = LGBMClassifier(
            objective="binary",
            metric="binary_logloss",
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
            y_val = extract_risk_target(val_orders)
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
        return [float(p) for p in self.model.predict_proba(X)[:, 1]]


def compute_risk_metrics(y_true: list[float], y_pred: list[float]) -> dict[str, float]:
    """Log-loss (primary), Brier score, and AUC for late-delivery probabilities."""
    if len(y_true) != len(y_pred):
        raise ValueError(f"Length mismatch: {len(y_true)} actual vs {len(y_pred)} predicted")
    if not y_true:
        raise ValueError("Cannot evaluate metrics on empty lists")
    if any(p < 0 or p > 1 for p in y_pred):
        raise ValueError("Risk predictions must be probabilities in [0, 1]")
    try:
        auc: float | None = round(float(roc_auc_score(y_true, y_pred)), 4)
    except ValueError:
        auc = None  # Single-class population; ranking is undefined.
    return {
        "log_loss": round(float(log_loss(y_true, y_pred, labels=[0.0, 1.0])), 4),
        "brier": round(float(brier_score_loss(y_true, y_pred)), 4),
        "auc": auc,
    }


def calibration_deciles(y_true: list[float], y_pred: list[float], bins: int = 10) -> list[dict]:
    """Mean predicted vs empirical late rate per predicted-probability decile."""
    order = np.argsort(y_pred)
    sorted_true = [y_true[i] for i in order]
    sorted_pred = [y_pred[i] for i in order]
    table = []
    for chunk_true, chunk_pred in zip(np.array_split(sorted_true, bins), np.array_split(sorted_pred, bins)):
        table.append({"count": len(chunk_true),
                      "mean_predicted": round(float(np.mean(chunk_pred)), 4),
                      "empirical_rate": round(float(np.mean(chunk_true)), 4)})
    return table


def segment_log_loss(orders: list[dict], predictions: list[float]) -> dict[str, dict]:
    """Log-loss by observed confirmation-time weather; absent groups not evaluated."""
    groups: dict[str, list[int]] = {}
    for index, order in enumerate(orders):
        groups.setdefault(order["weather_type"], []).append(index)
    return {weather: {"count": len(indices),
                      "log_loss": round(float(log_loss(
                          [float(orders[i]["late_delivery"]) for i in indices],
                          [predictions[i] for i in indices], labels=[0.0, 1.0])), 4)}
            for weather, indices in sorted(groups.items())}


def evaluate_risk_models(
    splits: dict[str, list[dict]], *, include_test: bool = False,
) -> tuple[dict[str, dict[str, dict]], LightGBMRiskModel]:
    """Compare frozen risk models; validation selects the tree count, never test."""
    train_orders = splits["train"]
    val_orders = splits["val"]

    constant = ConstantRiskBaseline().fit(train_orders)
    eta_model = LightGBMETAModel().fit(train_orders, val_orders=val_orders)
    risk_model = LightGBMRiskModel().fit(train_orders, val_orders=val_orders)

    models = {
        "Constant Rate": constant,
        "ETA Threshold": ETAThresholdBaseline(eta_model),
        "LightGBM Risk": risk_model,
    }

    results: dict[str, dict[str, dict]] = {}
    for model_name, model in models.items():
        results[model_name] = {}
        for split_name in (("train", "val", "test") if include_test else ("train", "val")):
            orders = splits[split_name]
            y_true = [float(o["late_delivery"]) for o in orders]
            y_pred = model.predict(orders)
            metrics = {"count": len(orders), **compute_risk_metrics(y_true, y_pred)}
            if split_name != "train":
                metrics["calibration"] = calibration_deciles(y_true, y_pred)
                metrics["weather"] = segment_log_loss(orders, y_pred)
            results[model_name][split_name] = metrics

    return results, risk_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and evaluate the late-delivery risk model.")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data/orders_2026_jan_aug.json"),
        help="Path to orders JSON file.",
    )
    parser.add_argument("--include-test", action="store_true", help="Explicitly score the final test set; do not tune on it.")
    parser.add_argument("--segments", action="store_true", help="Print validation/test weather log-loss in nats.")
    args = parser.parse_args()

    dataset = prepare_dataset(args.data)
    splits = dataset["splits"]

    print("Synthetic data only. Test scoring is " + ("enabled." if args.include_test else "disabled."))
    results, risk_model = evaluate_risk_models(splits, include_test=args.include_test)

    print("\n" + "=" * 64)
    print(f"{'Model':<16} | {'Split':<6} | {'LogLoss':<8} | {'Brier':<7} | {'AUC':<6}")
    print("-" * 64)
    for model_name, split_metrics in results.items():
        for split_name, m in split_metrics.items():
            auc = f"{m['auc']:.4f}" if m["auc"] is not None else "n/a"
            print(f"{model_name:<16} | {split_name.upper():<6} | "
                  f"{m['log_loss']:<8.4f} | {m['brier']:<7.4f} | {auc:<6}")
        print("-" * 64)

    if args.segments:
        for model_name, split_metrics in results.items():
            for split_name, m in split_metrics.items():
                for weather, s in m.get("weather", {}).items():
                    print(f"{model_name} / {split_name.upper()} / {weather}: "
                          f"n={s['count']} log-loss={s['log_loss']:.4f}")

    best_iter = getattr(risk_model.model, "best_iteration_", None)
    if best_iter:
        print(f"\nRisk validation-selected iteration: {best_iter} / {risk_model.n_estimators}")


if __name__ == "__main__":
    main()
