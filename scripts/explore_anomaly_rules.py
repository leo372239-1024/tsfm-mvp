"""Compare alarm suppression rules for the NAB realTraffic subset.

Development-set exploration: replays the causal detector once per file, then
applies candidate suppression rules (cooldown only, or cooldown with a
hysteresis re-arm level) and scores each with the NAB Sweeper (subset
normalization, see scripts/benchmark_anomaly.py). Read-only: prints a table,
writes nothing.
"""
from collections import deque
from datetime import timedelta
from pathlib import Path
import json
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.anomaly import assess  # noqa: E402
from third_party.nab_sweeper import Sweeper  # noqa: E402

THRESHOLD = 6.0
RULES = [
    {"name": "c12 (current)", "cooldown": 12.0, "rearm": None},
    {"name": "c6", "cooldown": 6.0, "rearm": None},
    {"name": "c3", "cooldown": 3.0, "rearm": None},
    {"name": "c1", "cooldown": 1.0, "rearm": None},
    {"name": "c12 rearm3", "cooldown": 12.0, "rearm": 3.0},
    {"name": "c3 rearm3", "cooldown": 3.0, "rearm": 3.0},
    {"name": "c1 rearm3", "cooldown": 1.0, "rearm": 3.0},
    {"name": "c6 rearm3", "cooldown": 6.0, "rearm": 3.0},
    {"name": "c4 rearm3", "cooldown": 4.0, "rearm": 3.0},
    {"name": "c2 rearm3", "cooldown": 2.0, "rearm": 3.0},
    {"name": "c3 rearm2", "cooldown": 3.0, "rearm": 2.0},
    {"name": "c3 rearm4", "cooldown": 3.0, "rearm": 4.0},
]


def replay():
    labels = json.loads((ROOT / "data/nab/combined_windows.json").read_text(encoding="utf-8"))
    bundles = []
    for path in sorted((ROOT / "data/nab/realTraffic").glob("*.csv")):
        frame = pd.read_csv(path, parse_dates=["timestamp"]).sort_values("timestamp")
        timestamps = [t.to_pydatetime() for t in frame["timestamp"]]
        values = frame["value"].astype(float).tolist()
        key = f"realTraffic/{path.name}"
        windows = [(pd.Timestamp(a).to_pydatetime(), pd.Timestamp(b).to_pydatetime())
                   for a, b in labels[key]]
        diffs = frame["timestamp"].diff().dt.total_seconds().dropna()
        cadence = int(diffs[diffs > 0].median())
        history = deque(maxlen=max(32, int(15 * 86400 / cadence)))
        points = []
        for ts, value in zip(timestamps, values):
            verdict = assess(list(history), ts, value, cadence, THRESHOLD)
            points.append({"ts": ts, "eligible": verdict["status"] == "alert",
                           "score": float(verdict.get("score", 0.0))})
            history.append({"ts": ts, "value": value})
        bundles.append({"key": key, "timestamps": timestamps, "points": points, "windows": windows})
    return bundles


def select_alerts(points, cooldown_hours, rearm):
    cooldown = timedelta(hours=cooldown_hours)
    alerts = []
    last = None
    armed = True
    for point in points:
        if rearm is not None and point["score"] < rearm:
            armed = True
        if point["eligible"] and armed and (last is None or point["ts"] - last >= cooldown):
            alerts.append(point["ts"])
            last = point["ts"]
            armed = rearm is None
    return alerts


def aggregate(bundles, alerts_by_key):
    windows = [(b["key"], a, b_) for b in bundles for a, b_ in b["windows"]]
    predictions = [(b["key"], t) for b in bundles for t in alerts_by_key[b["key"]]]
    tp = sum(any(k == fk and a <= t <= b for fk, t in predictions) for k, a, b in windows)
    fp = sum(not any(k == fk and a <= t <= b for fk, a, b in windows) for k, t in predictions)
    days = sum((b["timestamps"][-1] - b["timestamps"][0]).total_seconds() / 86400 for b in bundles)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / len(windows)
    return {"windows": len(windows), "detected": tp, "false_alerts": fp,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
            "fp_per_day": round(fp / days, 3)}


def nab_standard(bundles, alerts_by_key):
    sweeper = Sweeper(probationPercent=0.15,
                      costMatrix={"tpWeight": 1.0, "fpWeight": 0.11, "fnWeight": 1.0})
    candidate, control, perfect = set(), set(), set()
    for b in bundles:
        for t in alerts_by_key[b["key"]]:
            candidate.add((b["key"], t))
        for left, right in b["windows"]:
            perfect.add((b["key"], next(t for t in b["timestamps"] if left <= t <= right)))
    null_raw, perfect_raw, candidate_raw, baseline_raw = 0.0, 0.0, 0.0, 0.0
    for b in bundles:
        def raw(active):
            scores = [1.0 if (b["key"], t) in active else 0.0 for t in b["timestamps"]]
            return float(sweeper.scoreDataSet(b["timestamps"], scores, b["windows"], b["key"], 0.5)[1].score)
        candidate_raw += raw(candidate)
        perfect_raw += raw(perfect)
        null_raw += raw(set())
    return round(100 * (candidate_raw - null_raw) / (perfect_raw - null_raw), 3)


def main():
    bundles = replay()
    for rule in RULES:
        alerts_by_key = {b["key"]: select_alerts(b["points"], rule["cooldown"], rule["rearm"])
                         for b in bundles}
        metrics = aggregate(bundles, alerts_by_key)
        standard = nab_standard(bundles, alerts_by_key)
        print(f"{rule['name']:>12} | std {standard:6.3f} | {metrics['detected']:2d}/{metrics['windows']} "
              f"| FP {metrics['false_alerts']:3d} | P {metrics['precision']:.3f} | F1 {metrics['f1']:.3f} "
              f"| FP/day {metrics['fp_per_day']:.3f}")


if __name__ == "__main__":
    main()
