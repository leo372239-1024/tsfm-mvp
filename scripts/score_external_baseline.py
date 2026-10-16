"""Score NAB's official numentaTM detector results on the 7 realTraffic files.

Reproduces NAB's optimize -> score -> normalize steps restricted to the
traffic subset, using the upstream MIT-licensed Sweeper mirrored in
third_party/nab_sweeper.py. The normalized number is subset-internal
(null = -14, perfect = +14 on 14 labeled windows); it is NOT the official
58-file leaderboard score. Thresholds are optimized on these same labeled
windows, exactly as NAB does for its published detector scores, so this is a
development-set comparison.

The script also scores this project's detector score stream under the same
pipeline as a diagnostic. The production operating rule additionally applies
an absolute-deviation guard and a 12 h cooldown, which the NAB pipeline does
not model; the diagnostic therefore isolates the raw score signal.
"""
from hashlib import sha256
from pathlib import Path
from collections import deque
import base64
import json
import sys
import time

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.anomaly import assess  # noqa: E402
from third_party.nab_sweeper import Sweeper  # noqa: E402

NAB_PIN = "ea702d75cc2258d9d7dd35ca8e5e2539d71f3140"
RESULT_DIR = ROOT / "data/nab/results_numentaTM/realTraffic"
LABELS = ROOT / "data/nab/combined_windows.json"
DATA_DIR = ROOT / "data/nab/realTraffic"
COST_MATRIX = {"tpWeight": 1.0, "fpWeight": 0.11, "fnWeight": 1.0}
PROBATION = 0.15

NAMES = ["numentaTM_TravelTime_387.csv", "numentaTM_TravelTime_451.csv",
         "numentaTM_occupancy_6005.csv", "numentaTM_occupancy_t4013.csv",
         "numentaTM_speed_6005.csv", "numentaTM_speed_7578.csv",
         "numentaTM_speed_t4013.csv"]


def ensure_results():
    """Download pinned numentaTM result CSVs; return file digests.

    Also records missing entries in data/manifest.json so the provenance of
    these upstream baseline files stays with the other source digests.
    """
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    digests = {}
    for name in NAMES:
        path = RESULT_DIR / name
        url = (f"https://api.github.com/repos/numenta/NAB/contents/"
               f"results/numentaTM/realTraffic/{name}?ref={NAB_PIN}")
        if not path.exists():
            for attempt in range(4):
                try:
                    response = requests.get(url, timeout=90)
                    response.raise_for_status()
                    path.write_bytes(base64.b64decode(response.json()["content"]))
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(2 + 2 * attempt)
        digests[name] = sha256(path.read_bytes()).hexdigest()
        manifest_path = ROOT / "data/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
        if not any(s.get("path") == relative for s in manifest["sources"]):
            manifest["sources"].append({"url": url, "path": relative,
                                        "bytes": path.stat().st_size, "sha256": digests[name]})
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return digests


def load_bundles():
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    bundles = []
    for result_path in sorted(RESULT_DIR.glob("numentaTM_*.csv")):
        raw_name = result_path.name.replace("numentaTM_", "")
        key = f"realTraffic/{raw_name}"
        data = pd.read_csv(DATA_DIR / raw_name, parse_dates=["timestamp"])
        data = data.sort_values("timestamp")
        scores = pd.read_csv(result_path, parse_dates=["timestamp"])
        scores = scores.sort_values("timestamp")
        assert len(data) == len(scores), key
        assert (data["timestamp"].to_numpy() == scores["timestamp"].to_numpy()).all(), key
        timestamps = [t.to_pydatetime() for t in data["timestamp"]]
        windows = [(pd.Timestamp(a).to_pydatetime(), pd.Timestamp(b).to_pydatetime())
                   for a, b in labels[key]]
        bundles.append({"key": key, "timestamps": timestamps,
                        "values": data["value"].astype(float).tolist(),
                        "numenta_scores": scores["anomaly_score"].astype(float).tolist(),
                        "windows": windows})
    return bundles


def sweep_rows(sweeper, bundles, scores_key):
    rows = []
    for bundle in bundles:
        rows.extend(sweeper.calcSweepScore(bundle["timestamps"], bundle[scores_key],
                                           bundle["windows"], bundle["key"]))
    return rows


def best_threshold_score(rows):
    sweeper = Sweeper(probationPercent=PROBATION, costMatrix=COST_MATRIX)
    return max(sweeper.calcScoreByThreshold(rows), key=lambda x: x.score)


def normalize(raw, null, perfect):
    return round(100.0 * (raw - null) / (perfect - null), 3)


def candidate_scores(bundles):
    """Causal replay of the production detector's continuous score stream."""
    output = []
    for bundle in bundles:
        timestamps, values = bundle["timestamps"], bundle["values"]
        diffs = pd.Series(timestamps).diff().dt.total_seconds().dropna()
        cadence = int(diffs[diffs > 0].median())
        history = deque(maxlen=max(32, int(15 * 86400 / cadence)))
        scores = []
        for ts, value in zip(timestamps, values):
            verdict = assess(list(history), ts, value, cadence)
            scores.append(float(verdict.get("score", 0.0)))
            history.append({"ts": ts, "value": value})
        output.append({**bundle, "candidate_scores": scores})
    return output


def main():
    digests = ensure_results()
    bundles = load_bundles()
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    windows_total = sum(len(labels[b["key"]]) for b in bundles)

    sweeper = Sweeper(probationPercent=PROBATION, costMatrix=COST_MATRIX)

    # Sanity: null (all zeros) and perfect (first point of each window).
    null_bundles = [dict(b, zero_scores=[0.0] * len(b["timestamps"])) for b in bundles]
    null_best = best_threshold_score(sweep_rows(sweeper, null_bundles, "zero_scores"))
    perfect_bundles = []
    for b in bundles:
        hits = set()
        for left, right in b["windows"]:
            hits.add(next(t for t in b["timestamps"] if left <= t <= right))
        perfect_bundles.append(dict(b, perfect_scores=[1.0 if t in hits else 0.0
                                                       for t in b["timestamps"]]))
    perfect_best = best_threshold_score(sweep_rows(sweeper, perfect_bundles, "perfect_scores"))

    numenta_best = best_threshold_score(sweep_rows(sweeper, bundles, "numenta_scores"))
    candidate_bundles = candidate_scores(bundles)
    candidate_best = best_threshold_score(sweep_rows(sweeper, candidate_bundles, "candidate_scores"))

    ref_path = ROOT / "artifacts/anomaly_benchmark_t6_c3_rearm3.json"
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    subset = ref["nab_standard_realTraffic_subset"]
    reference = {
        "note": (f"from artifacts/{ref_path.name}; deployed rule: fixed threshold "
                 f"{ref['threshold']}, {ref['cooldown_hours']}h cooldown, re-arm below "
                 f"{ref['rearm_score']}; not threshold-optimized"),
        "raw_best_score": subset["raw"]["candidate"],
        "subset_normalized_score": subset["candidate"],
    }

    result = {
        "protocol": ("NAB official optimize->score->normalize restricted to the 7 "
                     "realTraffic files; standard cost matrix; 15% probation; "
                     "thresholds optimized on these same labeled windows, as NAB "
                     "does for its published detector scores"),
        "pinned_commit": NAB_PIN,
        "files": {name: digest for name, digest in sorted(digests.items())},
        "labeled_windows": windows_total,
        "sanity": {"null_best_score": null_best.score, "perfect_best_score": perfect_best.score},
        "numentaTM": {
            "optimized_threshold": numenta_best.threshold,
            "raw_best_score": round(numenta_best.score, 4),
            "points_tp_fp_fn": [numenta_best.tp, numenta_best.fp, numenta_best.fn],
            "subset_normalized_score": normalize(numenta_best.score, null_best.score, perfect_best.score),
        },
        "candidate_continuous_diagnostic": {
            "note": ("diagnostic only: detector score stream without the production "
                     "absolute guard and 12h cooldown; scores quantized to 3 decimals "
                     "by the assess() contract"),
            "optimized_threshold": candidate_best.threshold,
            "raw_best_score": round(candidate_best.score, 4),
            "points_tp_fp_fn": [candidate_best.tp, candidate_best.fp, candidate_best.fn],
            "subset_normalized_score": normalize(candidate_best.score, null_best.score, perfect_best.score),
        },
        "candidate_fixed_operating_point_reference": reference,
    }

    out = ROOT / "artifacts/anomaly_external_baseline.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
