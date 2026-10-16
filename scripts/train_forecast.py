"""Chronological one-hour-ahead traffic benchmark and production model."""
from pathlib import Path
import json
import sys
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.forecast import FEATURES, LAGS  # noqa: E402


def load_examples():
    frame = pd.read_csv(ROOT / "data/metro_hourly.csv", parse_dates=["ts"])
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame = frame.set_index("ts").sort_index()
    values = frame["value"].astype(float).asfreq("h")
    features = pd.DataFrame(index=values.index)
    for lag in LAGS:
        features[f"lag_{lag}"] = values.shift(lag)
    features["mean_24"] = values.shift(1).rolling(24, min_periods=24).mean()
    features["mean_168"] = values.shift(1).rolling(168, min_periods=168).mean()
    hour = values.index.hour
    dow = values.index.dayofweek
    features["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    features["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    features["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    features["dow_cos"] = np.cos(2 * np.pi * dow / 7)
    features["month"] = values.index.month
    valid = features.notna().all(axis=1) & values.notna()
    return values[valid], features.loc[valid, FEATURES]


def metrics(actual, predicted):
    actual = np.asarray(actual)
    predicted = np.asarray(predicted)
    return {"MAE": round(float(mean_absolute_error(actual, predicted)), 3),
            "RMSE": round(float(np.sqrt(mean_squared_error(actual, predicted))), 3),
            "WAPE": round(float(np.sum(np.abs(actual - predicted)) / np.sum(np.abs(actual))), 5)}


def main():
    target, X = load_examples()
    if len(target) < 1000:
        raise RuntimeError("too few complete windows")
    times = target.index
    y = target.to_numpy()
    train = times < pd.Timestamp("2017-01-01", tz="UTC")
    validation = (times >= pd.Timestamp("2017-01-01", tz="UTC")) & (times < pd.Timestamp("2018-01-01", tz="UTC"))
    test = times >= pd.Timestamp("2018-01-01", tz="UTC")
    if min(int(train.sum()), int(validation.sum()), int(test.sum())) < 500:
        raise RuntimeError("insufficient chronological split")
    # Fixed candidate selected before evaluating the 2018 holdout.
    model = HistGradientBoostingRegressor(max_iter=200, max_leaf_nodes=31, learning_rate=0.07,
                                           l2_regularization=1.0, random_state=42)
    model.fit(X[train], y[train])
    validation_metrics = metrics(y[validation], model.predict(X[validation]))
    # Refit on past-only train+validation, then score test once.
    model.fit(X[train | validation], y[train | validation])
    prediction = np.maximum(0, model.predict(X[test]))
    result = {"protocol": "one-step ahead, past 168 hours only, fixed UTC-06 wall clock, no future covariates",
              "split": {"train": int(train.sum()), "validation": int(validation.sum()), "test": int(test.sum()),
                        "test_start": str(times[test][0]), "test_end": str(times[test][-1])},
              "model": "HistGradientBoostingRegressor-v1", "validation": validation_metrics,
              "test": metrics(y[test], prediction),
              "baselines": {"yesterday_same_hour": metrics(y[test], X.loc[test, "lag_24"]),
                            "last_week_same_hour": metrics(y[test], X.loc[test, "lag_168"])}}
    out = ROOT / "artifacts"
    out.mkdir(exist_ok=True)
    joblib.dump(model, out / "traffic_model.joblib")
    (out / "forecast_benchmark.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

