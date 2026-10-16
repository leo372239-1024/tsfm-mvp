"""Experimental direct 1..8 hour model; evaluate on the same 2018 origins."""
from pathlib import Path
import json
import sys
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import HistGradientBoostingRegressor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.forecast import FEATURES, LAGS  # noqa: E402
from scripts.train_forecast import metrics  # noqa: E402

DIRECT_FEATURES = FEATURES + ["horizon"]


def make_features(series):
    base = pd.DataFrame(index=series.index)
    for lag in LAGS:
        base[f"lag_{lag}"] = series.shift(lag - 1)
    base["mean_24"] = series.rolling(24, min_periods=24).mean()
    base["mean_168"] = series.rolling(168, min_periods=168).mean()
    parts = []
    for h in range(1, 9):
        future = series.index + pd.Timedelta(hours=h)
        x = base.copy()
        x["hour_sin"] = np.sin(2 * np.pi * future.hour / 24)
        x["hour_cos"] = np.cos(2 * np.pi * future.hour / 24)
        x["dow_sin"] = np.sin(2 * np.pi * future.dayofweek / 7)
        x["dow_cos"] = np.cos(2 * np.pi * future.dayofweek / 7)
        x["month"] = future.month
        x["horizon"] = h
        x["target"] = series.shift(-h)
        x["target_time"] = future
        x["origin"] = series.index
        parts.append(x.dropna())
    return pd.concat(parts, ignore_index=True)


def main():
    raw = pd.read_csv(ROOT / "data/metro_hourly.csv")
    raw["ts"] = pd.to_datetime(raw["ts"], utc=True)
    series = raw.set_index("ts")["value"].astype(float).sort_index().asfreq("h")
    all_rows = make_features(series)
    train = all_rows["target_time"] < pd.Timestamp("2017-01-01", tz="UTC")
    val = (all_rows["target_time"] >= pd.Timestamp("2017-01-01", tz="UTC")) & (all_rows["target_time"] < pd.Timestamp("2018-01-01", tz="UTC"))
    test = all_rows["target_time"] >= pd.Timestamp("2018-01-01", tz="UTC")
    model = HistGradientBoostingRegressor(max_iter=240, max_leaf_nodes=63, learning_rate=0.05,
                                           l2_regularization=1.0, random_state=42)
    model.fit(all_rows.loc[train, DIRECT_FEATURES], all_rows.loc[train, "target"])
    val_metrics = metrics(all_rows.loc[val, "target"], model.predict(all_rows.loc[val, DIRECT_FEATURES]))
    model.fit(all_rows.loc[train | val, DIRECT_FEATURES], all_rows.loc[train | val, "target"])
    test_rows = all_rows.loc[test].copy()
    # Same daily origins and complete 8-hour windows as benchmark_horizon.py.
    origin_start = pd.Timestamp("2018-01-01", tz="UTC")
    daily_origins = set(series.loc[origin_start:].index[::24])
    test_rows = test_rows[test_rows["origin"].isin(daily_origins)]
    complete = test_rows.groupby("origin")["horizon"].nunique()
    test_rows = test_rows[test_rows["origin"].isin(complete[complete == 8].index)]
    predicted = np.maximum(0, model.predict(test_rows[DIRECT_FEATURES]))
    result = {"protocol": "Direct multi-horizon, no future covariates, same 2018 daily origins",
              "train_rows": int(train.sum()), "validation_rows": int(val.sum()),
              "test_origins": int(test_rows["origin"].nunique()), "test_points": len(test_rows),
              "validation": val_metrics, "test": metrics(test_rows["target"], predicted)}
    (ROOT / "artifacts").mkdir(exist_ok=True)
    joblib.dump(model, ROOT / "artifacts/traffic_direct8.joblib")
    (ROOT / "artifacts/forecast_direct8_benchmark.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

