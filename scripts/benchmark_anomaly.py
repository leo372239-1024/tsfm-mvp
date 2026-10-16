"""Replay NAB realTraffic streams causally; event metrics are not official NAB score.

Suppression mirrors the deployed rule: cooldown window, plus a hysteresis
re-arm level the deviation must fall below after an alert before a new alert
is allowed. Pass --rearm-score 0 to disable hysteresis.
"""
from pathlib import Path
from collections import deque
from datetime import timedelta
import argparse
import json
import sys
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.anomaly import assess  # noqa: E402
from third_party.nab_sweeper import Sweeper  # noqa: E402


def baseline(history, value):
    if len(history) < 32:
        return False
    recent = np.asarray([x["value"] for x in history[-288:]], dtype=float)
    expected = np.median(recent)
    scale = max(1.4826 * np.median(np.abs(recent - expected)), 0.1 * abs(expected), 1.0)
    return abs(value - expected) >= max(5 * scale, 0.25 * abs(expected))


def score(predictions, windows, start, end):
    hits = [any(a >= left and a <= right for a in predictions) for left, right in windows]
    false = [a for a in predictions if not any(left <= a <= right for left, right in windows)]
    tp = sum(hits)
    precision = tp / (tp + len(false)) if tp + len(false) else 0.0
    recall = tp / len(windows) if windows else 0.0
    duration_days = max((end - start).total_seconds() / 86400, 1 / 24)
    return {"events": len(windows), "detected_events": tp, "false_alerts": len(false),
            "precision": round(precision, 4), "event_recall": round(recall, 4),
            "event_f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
            "false_alerts_per_day": round(len(false) / duration_days, 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=6.0)
    parser.add_argument("--cooldown-hours", type=float, default=3.0)
    parser.add_argument("--rearm-score", type=float, default=3.0,
                        help="deviation score that re-arms alerting after a fired alert; <=0 disables")
    args = parser.parse_args()
    rearm = args.rearm_score if args.rearm_score > 0 else None
    labels = json.loads((ROOT / "data/nab/combined_windows.json").read_text(encoding="utf-8"))
    file_results = []
    all_candidate = []
    all_baseline = []
    all_windows = []
    total_days = 0.0
    nab_raw = {"candidate": 0.0, "baseline": 0.0, "null": 0.0, "perfect": 0.0}
    for path in sorted((ROOT / "data/nab/realTraffic").glob("*.csv")):
        frame = pd.read_csv(path, parse_dates=["timestamp"])
        frame = frame.sort_values("timestamp")
        timestamps = [t.to_pydatetime() for t in frame["timestamp"]]
        values = frame["value"].astype(float).tolist()
        key = f"realTraffic/{path.name}"
        windows = [(pd.Timestamp(a).to_pydatetime(), pd.Timestamp(b).to_pydatetime()) for a, b in labels[key]]
        diffs = frame["timestamp"].diff().dt.total_seconds().dropna()
        cadence = int(diffs[diffs > 0].median())
        history = deque(maxlen=max(32, int(15 * 86400 / cadence)))
        candidate = []
        control = []
        candidate_armed = True
        for ts, value in zip(timestamps, values):
            prior = list(history)
            verdict = assess(prior, ts, value, cadence, args.threshold)
            point_score = verdict.get("score")
            if rearm is not None and point_score is not None and point_score < rearm:
                candidate_armed = True
            if verdict["status"] == "alert":
                inside_cooldown = candidate and ts - candidate[-1] < timedelta(hours=args.cooldown_hours)
                if not inside_cooldown and candidate_armed:
                    candidate.append(ts)
                    candidate_armed = rearm is None
            if baseline(prior, value) and (not control or ts - control[-1] >= timedelta(hours=args.cooldown_hours)):
                control.append(ts)
            history.append({"ts": ts, "value": value})
        c_score = score(candidate, windows, timestamps[0], timestamps[-1])
        b_score = score(control, windows, timestamps[0], timestamps[-1])
        sweeper = Sweeper(probationPercent=0.15,
                          costMatrix={"tpWeight": 1.0, "fpWeight": 0.11, "fnWeight": 1.0})
        candidate_set, control_set = set(candidate), set(control)
        perfect_set = {next((t for t in timestamps if left <= t <= right), None) for left, right in windows}
        def nab_score(active):
            scores = [1.0 if t in active else 0.0 for t in timestamps]
            return float(sweeper.scoreDataSet(timestamps, scores, windows, key, 0.5)[1].score)
        file_nab = {"candidate": nab_score(candidate_set), "baseline": nab_score(control_set),
                    "null": nab_score(set()), "perfect": nab_score(perfect_set)}
        for name, value in file_nab.items():
            nab_raw[name] += value
        file_results.append({"file": key, "points": len(values), "candidate": c_score, "baseline": b_score,
                             "nab_raw_standard": file_nab})
        # Namespace timestamps by file to avoid accidental cross-series matching.
        all_candidate.extend((key, t) for t in candidate)
        all_baseline.extend((key, t) for t in control)
        all_windows.extend((key, a, b) for a, b in windows)
        total_days += (timestamps[-1] - timestamps[0]).total_seconds() / 86400

    def aggregate(predictions):
        tp = sum(any(k == fk and a <= t <= b for fk, t in predictions) for k, a, b in all_windows)
        fp = sum(not any(k == fk and a <= t <= b for fk, a, b in all_windows) for k, t in predictions)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / len(all_windows)
        return {"events": len(all_windows), "detected_events": tp, "false_alerts": fp,
                "precision": round(precision, 4), "event_recall": round(recall, 4),
                "event_f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
                "false_alerts_per_day": round(fp / total_days, 3)}

    result = {"protocol": "NAB realTraffic, causal replay, labeled-window event metrics (not official NAB score)",
              "threshold": args.threshold, "cooldown_hours": args.cooldown_hours,
              "rearm_score": rearm,
              "candidate": aggregate(all_candidate), "baseline": aggregate(all_baseline),
              "nab_standard_realTraffic_subset": {
                  "candidate": round(100 * (nab_raw["candidate"] - nab_raw["null"]) / (nab_raw["perfect"] - nab_raw["null"]), 3),
                  "baseline": round(100 * (nab_raw["baseline"] - nab_raw["null"]) / (nab_raw["perfect"] - nab_raw["null"]), 3),
                  "raw": {k: round(v, 3) for k, v in nab_raw.items()},
                  "note": "Official NAB Sweeper, standard profile, 15% probation; normalized within 7 traffic files only, not the 58-file leaderboard score."},
              "per_file": file_results}
    out = ROOT / "artifacts"
    out.mkdir(exist_ok=True)
    name = f"anomaly_benchmark_t{args.threshold:g}_c{args.cooldown_hours:g}"
    if rearm is not None:
        name += f"_rearm{args.rearm_score:g}"
    (out / (name + ".json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"candidate": result["candidate"], "baseline": result["baseline"]}, indent=2))


if __name__ == "__main__":
    main()

