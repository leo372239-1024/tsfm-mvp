"""Eight-hour rolling-origin evaluation for the deployed recursive predictor."""
from pathlib import Path
import json
import sys
import numpy as np
import pandas as pd
import joblib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.forecast import FEATURES, feature_row  # noqa: E402
from scripts.train_forecast import metrics  # noqa: E402


def main():
    frame = pd.read_csv(ROOT / "data/metro_hourly.csv")
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    series = frame.set_index("ts")["value"].astype(float).sort_index().asfreq("h")
    model = joblib.load(ROOT / "artifacts/traffic_model.joblib")
    origins = []
    actual_all, candidate_all, day_all, week_all = [], [], [], []
    test_start = pd.Timestamp("2018-01-01", tz="UTC")
    for origin in series.loc[test_start:].index[::24]:
        context = series.loc[origin - pd.Timedelta(hours=167):origin]
        future = series.loc[origin + pd.Timedelta(hours=1):origin + pd.Timedelta(hours=8)]
        if len(context) != 168 or len(future) != 8 or context.isna().any() or future.isna().any():
            continue
        history = context.tolist()
        forecast = []
        for h in range(1, 9):
            ts = origin + pd.Timedelta(hours=h)
            value = max(0.0, float(model.predict(pd.DataFrame([feature_row(history, ts)], columns=FEATURES))[0]))
            forecast.append(value)
            history.append(value)
        origins.append(str(origin))
        actual_all.extend(future.tolist())
        candidate_all.extend(forecast)
        day_all.extend(context.iloc[-24:-16].tolist())
        week_all.extend(context.iloc[-168:-160].tolist())
    if len(origins) < 100:
        raise RuntimeError("too few rolling origins")
    result = {"protocol": "2018 holdout; 8-hour recursive forecast; 24-hour origin spacing; all 168 context and 8 target hours observed",
              "origins": len(origins), "target_points": len(actual_all),
              "first_origin": origins[0], "last_origin": origins[-1],
              "candidate": metrics(actual_all, candidate_all),
              "baselines": {"yesterday_same_hour": metrics(actual_all, day_all),
                            "last_week_same_hour": metrics(actual_all, week_all)},
              "per_horizon_mae": {str(h): round(float(np.mean(np.abs(np.asarray(actual_all)[h-1::8] - np.asarray(candidate_all)[h-1::8]))), 3)
                                  for h in range(1, 9)}}
    (ROOT / "artifacts/forecast_h8_benchmark.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

