"""Black-box acceptance on the running Compose API."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import statistics
import time
import uuid
import requests

ROOT = Path(__file__).resolve().parents[1]
env = dict(line.strip().split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if "=" in line)
BASE = f"http://127.0.0.1:{env.get('APP_PORT') or '27831'}"
SESSION = requests.Session()
SESSION.headers["X-API-Key"] = env["APP_API_KEY"]


def call(method, path, expected=200, **kwargs):
    response = SESSION.request(method, BASE + path, timeout=30, **kwargs)
    if response.status_code != expected:
        raise AssertionError(f"{method} {path}: {response.status_code} {response.text[:500]}")
    return response.json()


def main():
    assert requests.get(BASE + "/health/ready", timeout=10).status_code == 200
    assert requests.get(BASE + "/v1/assets", timeout=10).status_code == 401
    assets = call("GET", "/v1/assets")
    assert any(a["id"] == "MN-DOT-ATR-301" for a in assets)
    demo_fc = call("POST", "/v1/assets/MN-DOT-ATR-301/forecast", json={"horizon": 8, "provider": "local"})
    assert len(demo_fc["points"]) == 8 and demo_fc["model"] == "hist_gradient_boosting_direct8_v1"
    report = call("POST", "/v1/assets/MN-DOT-ATR-301/reports?days=7", expected=201)
    assert report["statistics"]["mean"] > 0 and report["data_quality"]["observed_points"] > 100
    assert len(report["reference_facts"]) >= 6 and not report["site_supplied_facts"]
    assert "# I-94 westbound ATR station 301" in report["markdown"]
    saved = call("GET", "/v1/reports/" + report["id"])
    assert saved["payload"]["statistics"] == report["statistics"]

    asset_id = "QA-" + uuid.uuid4().hex[:12]
    call("POST", "/v1/assets", expected=201, json={"id": asset_id, "name": "Acceptance sensor",
          "metric": "count", "unit": "vehicles/hour", "cadence_seconds": 3600})
    fact = {"subject_id": asset_id, "subject_label": "Acceptance sensor", "predicate": "MEASURES",
            "object_id": asset_id + ".count", "object_label": "count",
            "source_uri": "urn:qa:schematic-v1", "source_anchor": "Sheet QA-1 / symbol S1",
            "source_excerpt": "S1 -> count", "scope": "site_supplied"}
    call("POST", "/v1/knowledge/facts", expected=201, json=fact)
    saved_facts = call("GET", "/v1/knowledge/facts?scope=site_supplied")
    assert any(x["subject_id"] == asset_id and x["source_excerpt"] == fact["source_excerpt"] for x in saved_facts)
    conflicting_fact = SESSION.post(BASE + "/v1/knowledge/facts", json={**fact, "subject_label": "wrong label"}, timeout=10)
    assert conflicting_fact.status_code == 409
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    points = [{"ts": (start + timedelta(hours=i)).isoformat(), "value": 100.0} for i in range(48)]
    assert call("POST", f"/v1/assets/{asset_id}/backfill", json={"points": points})["inserted"] == 48
    assert call("POST", f"/v1/assets/{asset_id}/backfill", json={"points": points})["inserted"] == 0
    bad = SESSION.post(BASE + f"/v1/assets/{asset_id}/backfill", json={"points": [{**points[0], "value": 999}]}, timeout=10)
    assert bad.status_code == 409
    next_point = {"ts": (start + timedelta(hours=48)).isoformat(), "value": 1000.0}
    result = call("POST", f"/v1/assets/{asset_id}/telemetry", json=next_point)
    assert result["status"] == "alert" and result["alert_id"]
    assert call("POST", f"/v1/assets/{asset_id}/telemetry", json=next_point)["status"] == "duplicate"
    second = call("POST", f"/v1/assets/{asset_id}/telemetry", json={"ts": (start + timedelta(hours=49)).isoformat(), "value": 1000.0})
    assert second["status"] == "suppressed_by_cooldown"
    third = call("POST", f"/v1/assets/{asset_id}/telemetry", json={"ts": (start + timedelta(hours=51)).isoformat(), "value": 1000.0})
    assert third["status"] == "suppressed_by_hysteresis"
    for hour in range(52, 59):
        call("POST", f"/v1/assets/{asset_id}/telemetry", json={"ts": (start + timedelta(hours=hour)).isoformat(), "value": 100.0})
    rearmed = call("POST", f"/v1/assets/{asset_id}/telemetry", json={"ts": (start + timedelta(hours=59)).isoformat(), "value": 1000.0})
    assert rearmed["status"] == "alert" and rearmed["alert_id"]
    alerts = call("GET", "/v1/alerts?asset_id=" + asset_id)
    assert len(alerts) == 2 and all(a["state"] == "open" for a in alerts)
    assert call("POST", "/v1/alerts/" + result["alert_id"] + "/acknowledge")["state"] == "acknowledged"
    qa_report = call("POST", f"/v1/assets/{asset_id}/reports?days=7", expected=201)
    assert qa_report["alerts"]["acknowledged"] == 1
    assert len(qa_report["site_supplied_facts"]) == 1
    latencies = []
    for _ in range(30):
        begin = time.perf_counter()
        call("GET", "/v1/assets")
        latencies.append((time.perf_counter() - begin) * 1000)
    result_payload = {"status": "passed", "checks": ["readiness", "auth", "demo forecast", "report persistence",
                      "graph provenance", "backfill idempotency", "conflict", "realtime alarm", "cooldown",
                      "hysteresis re-arm", "acknowledgement"],
                      "api_get_assets_latency_ms": {"count": len(latencies), "p50": round(statistics.median(latencies), 2),
                                                    "p95": round(sorted(latencies)[int(0.95 * len(latencies)) - 1], 2)},
                      "qa_asset_id": asset_id, "demo_report_id": report["id"]}
    out = ROOT / "evidence"
    out.mkdir(exist_ok=True)
    (out / "smoke_test.json").write_text(json.dumps(result_payload, indent=2), encoding="utf-8")
    print(json.dumps(result_payload, indent=2))


if __name__ == "__main__":
    main()

