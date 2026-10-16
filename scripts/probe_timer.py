"""Live connectivity probe for Timer-3.5 through the configured Timecho endpoint.

Runs inside the API container where DATABASE_URL and TIMECHO_API_KEY are set.
Prints a keyless JSON summary only; never logs the API key.
"""
from pathlib import Path
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import connection, init_pool, close_pool  # noqa: E402
from app.forecast import forecast  # noqa: E402


def main():
    init_pool()
    try:
        with connection() as conn:
            asset = conn.execute("SELECT * FROM assets WHERE id='MN-DOT-ATR-301'").fetchone()
            rows = conn.execute("""SELECT ts,value FROM telemetry
                                   WHERE asset_id='MN-DOT-ATR-301' AND quality='measured'
                                   ORDER BY ts DESC LIMIT 720""").fetchall()
        rows = list(reversed(rows))
        begin = time.perf_counter()
        try:
            result = forecast(rows, 8, asset["cadence_seconds"], asset["id"], "timer")
            elapsed = (time.perf_counter() - begin) * 1000
            summary = {"status": "ok", "requested_provider": "timer", "model": result["model"],
                       "degraded_reason": result["degraded_reason"], "history_points": len(rows),
                       "history_end": result["history_end"], "horizon": 8,
                       "call_latency_ms": round(elapsed, 1),
                       "points": result["points"]}
        except Exception as exc:  # surface the failure honestly in evidence
            elapsed = (time.perf_counter() - begin) * 1000
            summary = {"status": "failed", "requested_provider": "timer",
                       "error_type": type(exc).__name__, "error": str(exc),
                       "history_points": len(rows), "horizon": 8,
                       "call_latency_ms": round(elapsed, 1)}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        close_pool()


if __name__ == "__main__":
    main()
