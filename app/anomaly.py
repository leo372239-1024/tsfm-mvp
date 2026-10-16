from datetime import timedelta
import numpy as np


def assess(rows, ts, value, cadence_seconds, threshold=6.0):
    """Causal robust seasonal rule. No labels or future readings are used."""
    if len(rows) < 32:
        return {"status": "insufficient_history", "sample_count": len(rows)}
    period = max(1, round(86400 / cadence_seconds))
    # Match prior daily phase within one interval; use a recent global window if sparse.
    by_time = {r["ts"]: float(r["value"]) for r in rows}
    peers = [by_time[ts - timedelta(days=day)] for day in range(1, 15)
             if ts - timedelta(days=day) in by_time]
    if len(peers) < 5:
        peers = [float(r["value"]) for r in rows[-min(len(rows), max(32, period)):]]
    expected = float(np.median(peers))
    mad = float(np.median(np.abs(np.asarray(peers) - expected)))
    scale = max(1.4826 * mad, 0.10 * abs(expected), 1.0)
    score = abs(value - expected) / scale
    # Traffic count uses an absolute guard so low-volume noise cannot flood alerts.
    absolute_guard = max(50.0, 0.25 * abs(expected)) if cadence_seconds >= 3600 else max(0.03, 0.25 * abs(expected))
    alert = score >= threshold and abs(value - expected) >= absolute_guard
    return {"status": "alert" if alert else "normal", "expected": round(expected, 3),
            "score": round(score, 3), "sample_count": len(peers),
            "reason": "causal_robust_seasonal_deviation"}

