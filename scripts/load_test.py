"""Bounded ten-stream ingestion load check on the deployed Compose stack."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
import json
import time
import uuid
import requests

ROOT = Path(__file__).resolve().parents[1]
env = dict(line.strip().split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if "=" in line)
BASE = f"http://127.0.0.1:{env.get('APP_PORT') or '27831'}"
HEADERS = {"X-API-Key": env["APP_API_KEY"]}
STREAMS = 10
POINTS_PER_STREAM = 100


def worker(asset_id, start):
    session = requests.Session()
    session.headers.update(HEADERS)
    timings = []
    statuses = []
    for i in range(POINTS_PER_STREAM):
        point = {"ts": (start + timedelta(hours=336 + i)).isoformat(), "value": 100.0}
        begin = time.perf_counter()
        response = session.post(f"{BASE}/v1/assets/{asset_id}/telemetry", json=point, timeout=20)
        timings.append((time.perf_counter() - begin) * 1000)
        statuses.append(response.status_code)
    return timings, statuses


def main():
    start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    batch = uuid.uuid4().hex[:8]
    asset_ids = []
    for index in range(STREAMS):
        asset_id = f"QA-LOAD-{batch}-{index}"
        asset_ids.append(asset_id)
        r = requests.post(BASE + "/v1/assets", headers=HEADERS, json={"id": asset_id,
                          "name": "Load test stream", "metric": "count",
                          "unit": "vehicles/hour", "cadence_seconds": 3600}, timeout=20)
        r.raise_for_status()
        points = [{"ts": (start + timedelta(hours=i)).isoformat(), "value": 100.0} for i in range(336)]
        r = requests.post(f"{BASE}/v1/assets/{asset_id}/backfill", headers=HEADERS,
                          json={"points": points}, timeout=30)
        r.raise_for_status()
        assert r.json()["inserted"] == 336
    begin = time.perf_counter()
    with ThreadPoolExecutor(max_workers=STREAMS) as pool:
        results = list(pool.map(lambda asset_id: worker(asset_id, start), asset_ids))
    elapsed = time.perf_counter() - begin
    timings = sorted(t for times, _ in results for t in times)
    statuses = [s for _, codes in results for s in codes]
    report = {"streams": STREAMS, "points_per_stream": POINTS_PER_STREAM,
              "requests": len(statuses), "http_200": statuses.count(200),
              "http_errors": len(statuses) - statuses.count(200),
              "elapsed_seconds": round(elapsed, 3), "throughput_rps": round(len(statuses) / elapsed, 2),
              "latency_ms": {"p50": round(median(timings), 2),
                             "p95": round(timings[int(0.95 * len(timings)) - 1], 2),
                             "p99": round(timings[int(0.99 * len(timings)) - 1], 2),
                             "max": round(timings[-1], 2)},
              "environment": "local Docker Desktop, one API container, 10 independent asset streams"}
    out = ROOT / "evidence"
    out.mkdir(exist_ok=True)
    (out / "ingest_load.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["http_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

