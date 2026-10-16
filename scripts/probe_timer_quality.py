"""Small live-API quality spot check: Timer-3.5 vs the deployed local model.

Three 8-hour blocks on the UCI I-94 series, single station. This is a spot
check, not a benchmark: 24 target points, live API calls with the configured
key. Prints a keyless JSON summary.
"""
from pathlib import Path
import json
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import DATA_PATH  # noqa: E402
from app.forecast import forecast  # noqa: E402

ASSET = "MN-DOT-ATR-301"
CANDIDATES = ["2018-06-04T05:00:00+00:00", "2018-07-09T05:00:00+00:00",
              "2018-08-27T05:00:00+00:00", "2018-09-03T05:00:00+00:00"]


def metrics(actual, predicted):
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    error = np.abs(actual - predicted)
    return {"mae": round(float(error.mean()), 3),
            "rmse": round(float(np.sqrt((error ** 2).mean())), 3),
            "wape_pct": round(float(100 * error.sum() / actual.sum()), 3)}


def main():
    frame = pd.read_csv(DATA_PATH)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame = frame.sort_values("ts")
    results = []
    for candidate in CANDIDATES:
        end = pd.Timestamp(candidate)
        history = frame[frame["ts"] <= end].tail(336)
        targets = frame[(frame["ts"] > end) & (frame["ts"] <= end + pd.Timedelta(hours=8))]
        if len(history) < 336 or len(targets) != 8:
            continue
        expected_ts = [end + pd.Timedelta(hours=i) for i in range(1, 9)]
        if list(targets["ts"]) != expected_ts:
            continue
        rows = [{"ts": ts, "value": value} for ts, value in zip(history["ts"], history["value"])]
        actual = targets["value"].astype(float).tolist()
        begin = time.perf_counter()
        timer_result = forecast(rows, 8, 3600, ASSET, "timer")
        timer_latency = (time.perf_counter() - begin) * 1000
        local_result = forecast(rows, 8, 3600, ASSET, "local")
        timer_values = [p["value"] for p in timer_result["points"]]
        local_values = [p["value"] for p in local_result["points"]]
        results.append({
            "history_end": timer_result["history_end"],
            "timer_model": timer_result["model"],
            "timer_degraded_reason": timer_result["degraded_reason"],
            "timer_call_latency_ms": round(timer_latency, 1),
            "actual": actual,
            "timer": timer_values,
            "local": local_values,
            "timer_metrics": metrics(actual, timer_values),
            "local_metrics": metrics(actual, local_values),
        })
        if len(results) == 3:
            break
    summary = {"probe": "Timer-3.5 live API vs deployed local model, 8h horizon spot check",
               "targets_total": sum(len(r["actual"]) for r in results),
               "blocks": results}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
