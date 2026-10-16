from datetime import timedelta
from pathlib import Path
import math
import numpy as np
import pandas as pd
import joblib
from .config import MODEL_PATH, TIMECHO_API_KEY

LAGS = [1, 2, 3, 24, 25, 48, 168]
FEATURES = [f"lag_{x}" for x in LAGS] + ["mean_24", "mean_168", "hour_sin", "hour_cos", "dow_sin", "dow_cos", "month"]


def feature_row(values, ts):
    if len(values) < 168:
        raise ValueError("168 hourly observations required for local model")
    hour = ts.hour
    dow = ts.weekday()
    return [float(values[-lag]) for lag in LAGS] + [
        float(np.mean(values[-24:])), float(np.mean(values[-168:])),
        math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24),
        math.sin(2 * math.pi * dow / 7), math.cos(2 * math.pi * dow / 7), ts.month,
    ]


def regular_history(rows, cadence_seconds):
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("no history")
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame = frame.sort_values("ts").drop_duplicates("ts", keep="last").set_index("ts")
    freq = f"{cadence_seconds}s"
    series = frame["value"].asfreq(freq)
    if series.isna().mean() > 0.15:
        raise ValueError("history has more than 15% missing intervals")
    return series.ffill().bfill()


def seasonal_predict(values, horizon, cadence_seconds):
    period = max(1, round(86400 / cadence_seconds))
    out = []
    for i in range(horizon):
        offset = (horizon - i)  # next value uses matching prior day phase
        candidates = []
        for day in range(1, 8):
            index = len(values) + i - day * period
            if index >= 0:
                candidates.append(values[index] if index < len(values) else out[index - len(values)])
        out.append(float(np.median(candidates)) if candidates else float(np.median(values[-min(24, len(values)):])))
    return out


def local_predict(series, horizon, cadence_seconds, asset_id):
    values = series.astype(float).tolist()
    if asset_id == "MN-DOT-ATR-301" and cadence_seconds == 3600 and len(values) >= 168 and Path(MODEL_PATH).exists():
        direct_path = Path(MODEL_PATH).with_name("traffic_direct8.joblib")
        if horizon <= 8 and direct_path.exists():
            direct = joblib.load(direct_path)
            rows = []
            for step in range(1, horizon + 1):
                ts = series.index[-1] + timedelta(hours=step)
                rows.append(feature_row(values, ts) + [step])
            predicted = np.maximum(0, direct.predict(pd.DataFrame(rows, columns=FEATURES + ["horizon"])))
            return predicted.astype(float).tolist(), "hist_gradient_boosting_direct8_v1"
        model = joblib.load(MODEL_PATH)
        output = []
        for step in range(1, horizon + 1):
            ts = series.index[-1] + timedelta(seconds=cadence_seconds * step)
            estimate = max(0.0, float(model.predict(pd.DataFrame([feature_row(values, ts)], columns=FEATURES))[0]))
            values.append(estimate)
            output.append(estimate)
        return output, "hist_gradient_boosting_v1"
    return seasonal_predict(values, horizon, cadence_seconds), "seasonal_median_v1"


def timer_predict(series, horizon, cadence_seconds):
    if not TIMECHO_API_KEY:
        raise RuntimeError("TIMECHO_API_KEY is not configured")
    from timecho_ai import TimechoAIClient
    target = pd.DataFrame({"time": series.index[-min(2880, len(series)):].tz_convert("UTC").tz_localize(None),
                           "target": series.iloc[-min(2880, len(series)):].to_numpy()})
    if len(target) < 16:
        raise ValueError("Timer-3.5 requires at least 16 points")
    result = TimechoAIClient(api_key=TIMECHO_API_KEY).forecast(
        data=target, target="target", output_length=horizon, model_id="Timer-3.5",
        output_start_time=target["time"].iloc[-1] + pd.Timedelta(seconds=cadence_seconds),
        time_col="time")
    first = result[0]
    if isinstance(first, pd.DataFrame):
        numeric = first.select_dtypes(include="number")
        if numeric.shape[1] != 1:
            raise RuntimeError("unexpected Timer response shape")
        values = numeric.iloc[:, 0].astype(float).tolist()
    elif isinstance(first, (list, tuple, np.ndarray)):
        values = [float(x) for x in first]
    else:
        raise RuntimeError("unexpected Timer response type")
    if len(values) != horizon or any(not math.isfinite(x) for x in values):
        raise RuntimeError("invalid Timer forecast")
    return [max(0.0, x) for x in values]


def forecast(rows, horizon, cadence_seconds, asset_id, provider="auto"):
    series = regular_history(rows, cadence_seconds)
    if len(series) < 16:
        raise ValueError("at least 16 observations required")
    degraded = None
    if provider in ("auto", "timer") and TIMECHO_API_KEY:
        try:
            predicted = timer_predict(series, horizon, cadence_seconds)
            model = "Timer-3.5"
        except Exception as exc:
            if provider == "timer":
                raise RuntimeError(f"Timer-3.5 failed: {type(exc).__name__}") from None
            degraded = type(exc).__name__
            predicted, model = local_predict(series, horizon, cadence_seconds, asset_id)
    elif provider == "timer":
        raise RuntimeError("TIMECHO_API_KEY is not configured")
    else:
        predicted, model = local_predict(series, horizon, cadence_seconds, asset_id)
    return {"model": model, "degraded_reason": degraded, "history_end": series.index[-1].isoformat(),
            "points": [{"ts": (series.index[-1] + timedelta(seconds=cadence_seconds * (i+1))).isoformat(),
                        "value": round(float(value), 3)} for i, value in enumerate(predicted)]}

