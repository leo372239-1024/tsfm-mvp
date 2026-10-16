from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import secrets
import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from . import config
from .anomaly import assess
from .db import connection, init_db, init_pool, close_pool
from .forecast import forecast
from .graph import add_fact, facts, init_graph
from .report import build_report
from .schemas import AssetIn, BackfillIn, FactIn, ForecastIn, PointIn


def bootstrap_demo():
    path = Path(config.DATA_PATH)
    if not path.exists():
        return
    asset = {"id": "MN-DOT-ATR-301", "name": "I-94 westbound ATR station 301",
             "metric": "traffic_volume", "unit": "vehicles/hour", "cadence_seconds": 3600,
             "source_uri": "https://archive.ics.uci.edu/dataset/492/metro+interstate+traffic+volume"}
    with connection() as conn:
        conn.execute("""INSERT INTO assets(id,name,metric,unit,cadence_seconds,source_uri)
                        VALUES (%(id)s,%(name)s,%(metric)s,%(unit)s,%(cadence_seconds)s,%(source_uri)s)
                        ON CONFLICT (id) DO NOTHING""", asset)
        count = conn.execute("SELECT count(*) AS n FROM telemetry WHERE asset_id=%s", (asset["id"],)).fetchone()["n"]
        if count:
            return
        with path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        # Historical demonstration window; later rows remain available for replay/evaluation.
        for row in rows[-672:-336]:
            conn.execute("""INSERT INTO telemetry(asset_id,ts,value,quality)
                            VALUES (%s,%s,%s,'measured') ON CONFLICT DO NOTHING""",
                         (asset["id"], row["ts"], float(row["value"])))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not config.API_KEY or len(config.API_KEY) < 24:
        raise RuntimeError("APP_API_KEY must be set to at least 24 characters")
    for attempt in range(30):
        try:
            init_pool()
            init_db()
            init_graph()
            bootstrap_demo()
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(2)
    yield
    close_pool()


app = FastAPI(title="Industrial Traffic Time Series MVP", version="0.1.0", lifespan=lifespan)


def authenticate(x_api_key: str | None = Header(default=None)):
    if not x_api_key or not secrets.compare_digest(x_api_key, config.API_KEY):
        raise HTTPException(status_code=401, detail="invalid API key")


@app.get("/health/live")
def liveness():
    return {"status": "alive"}


@app.get("/health/ready")
def readiness():
    try:
        with connection() as conn:
            conn.execute("SELECT 1")
        facts("reference")
        return {"status": "ready"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})


@app.post("/v1/assets", dependencies=[Depends(authenticate)], status_code=201)
def create_asset(payload: AssetIn):
    with connection() as conn:
        existing = conn.execute("SELECT * FROM assets WHERE id=%s", (payload.id,)).fetchone()
        if existing:
            raise HTTPException(409, "asset id already exists")
        conn.execute("""INSERT INTO assets(id,name,metric,unit,cadence_seconds,source_uri)
                        VALUES (%s,%s,%s,%s,%s,%s)""",
                     (payload.id, payload.name, payload.metric, payload.unit,
                      payload.cadence_seconds, payload.source_uri))
    return payload.model_dump()


@app.get("/v1/assets", dependencies=[Depends(authenticate)])
def list_assets():
    with connection() as conn:
        return conn.execute("SELECT * FROM assets ORDER BY id").fetchall()


def get_asset(conn, asset_id):
    asset = conn.execute("SELECT * FROM assets WHERE id=%s", (asset_id,)).fetchone()
    if not asset:
        raise HTTPException(404, "asset not found")
    return asset


@app.post("/v1/assets/{asset_id}/telemetry", dependencies=[Depends(authenticate)])
def ingest(asset_id: str, point: PointIn):
    if point.ts.tzinfo is None:
        raise HTTPException(422, "timestamp must include timezone")
    alert_id = None
    with connection() as conn:
        asset = get_asset(conn, asset_id)
        conn.execute("SELECT id FROM assets WHERE id=%s FOR UPDATE", (asset_id,))
        existing = conn.execute("SELECT value,quality FROM telemetry WHERE asset_id=%s AND ts=%s",
                                (asset_id, point.ts)).fetchone()
        if existing:
            if existing["value"] != point.value or existing["quality"] != point.quality:
                raise HTTPException(409, "conflicting value for timestamp")
            return {"status": "duplicate", "alert_id": None}
        latest = conn.execute("SELECT max(ts) AS ts FROM telemetry WHERE asset_id=%s", (asset_id,)).fetchone()["ts"]
        if latest and point.ts > latest:
            rows = conn.execute("""SELECT ts,value FROM telemetry WHERE asset_id=%s AND ts >= %s AND ts < %s
                                   ORDER BY ts""", (asset_id, point.ts - timedelta(days=15), point.ts)).fetchall()
            assessment = assess(rows, point.ts, point.value, asset["cadence_seconds"])
        else:
            assessment = {"status": "late_or_first"}
        conn.execute("INSERT INTO telemetry(asset_id,ts,value,quality) VALUES (%s,%s,%s,%s)",
                     (asset_id, point.ts, point.value, point.quality))
        state = conn.execute("""SELECT last_alert_ts, armed FROM detector_state
                                WHERE asset_id=%s FOR UPDATE""", (asset_id,)).fetchone()
        if state is None:
            conn.execute("""INSERT INTO detector_state(asset_id) VALUES (%s)
                            ON CONFLICT (asset_id) DO NOTHING""", (asset_id,))
            state = {"last_alert_ts": None, "armed": True}
        if assessment["status"] == "alert" and point.quality == "measured":
            inside_cooldown = state["last_alert_ts"] is not None and \
                (point.ts - state["last_alert_ts"]) < timedelta(hours=config.ALERT_COOLDOWN_HOURS)
            if inside_cooldown:
                assessment["status"] = "suppressed_by_cooldown"
            elif not state["armed"]:
                assessment["status"] = "suppressed_by_hysteresis"
            else:
                alert_id = uuid.uuid4()
                conn.execute("""INSERT INTO alerts(id,asset_id,ts,value,expected,score,reason)
                                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                             (alert_id, asset_id, point.ts, point.value, assessment["expected"],
                              assessment["score"], assessment["reason"]))
                conn.execute("""UPDATE detector_state SET last_alert_ts=%s, armed=false,
                                updated_at=now() WHERE asset_id=%s""", (point.ts, asset_id))
        if assessment.get("score") is not None and assessment["score"] < config.ALERT_REARM_SCORE:
            conn.execute("""UPDATE detector_state SET armed=true, updated_at=now()
                            WHERE asset_id=%s AND armed=false""", (asset_id,))
    return {"status": assessment["status"], "assessment": assessment,
            "alert_id": str(alert_id) if alert_id else None}


@app.post("/v1/assets/{asset_id}/backfill", dependencies=[Depends(authenticate)])
def backfill(asset_id: str, payload: BackfillIn):
    if any(p.ts.tzinfo is None for p in payload.points):
        raise HTTPException(422, "timestamps must include timezone")
    with connection() as conn:
        get_asset(conn, asset_id)
        inserted = 0
        for point in payload.points:
            cursor = conn.execute("""INSERT INTO telemetry(asset_id,ts,value,quality) VALUES (%s,%s,%s,%s)
                                     ON CONFLICT DO NOTHING""",
                                  (asset_id, point.ts, point.value, point.quality))
            inserted += cursor.rowcount
            if cursor.rowcount == 0:
                existing = conn.execute("SELECT value,quality FROM telemetry WHERE asset_id=%s AND ts=%s",
                                        (asset_id, point.ts)).fetchone()
                if existing["value"] != point.value or existing["quality"] != point.quality:
                    raise HTTPException(409, "conflicting value for timestamp")
    return {"inserted": inserted, "ignored_duplicates": len(payload.points) - inserted,
            "alarms_evaluated": False}


@app.get("/v1/assets/{asset_id}/telemetry", dependencies=[Depends(authenticate)])
def telemetry(asset_id: str, limit: int = Query(default=200, ge=1, le=10000)):
    with connection() as conn:
        get_asset(conn, asset_id)
        rows = conn.execute("SELECT ts,value,quality FROM telemetry WHERE asset_id=%s ORDER BY ts DESC LIMIT %s",
                            (asset_id, limit)).fetchall()
    return list(reversed(rows))


@app.post("/v1/assets/{asset_id}/forecast", dependencies=[Depends(authenticate)])
def predict(asset_id: str, request: ForecastIn):
    with connection() as conn:
        asset = get_asset(conn, asset_id)
        rows = conn.execute("""SELECT ts,value FROM telemetry WHERE asset_id=%s AND quality='measured'
                               ORDER BY ts DESC LIMIT 2880""", (asset_id,)).fetchall()
    try:
        result = forecast(list(reversed(rows)), request.horizon, asset["cadence_seconds"], asset_id, request.provider)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None
    return {"asset_id": asset_id, "unit": asset["unit"], **result}


@app.get("/v1/alerts", dependencies=[Depends(authenticate)])
def list_alerts(asset_id: str | None = None, state: str | None = None, limit: int = Query(default=100, ge=1, le=1000)):
    if state not in (None, "open", "acknowledged"):
        raise HTTPException(422, "invalid state")
    clauses = ["TRUE"]
    params = []
    if asset_id is not None:
        clauses.append("asset_id=%s")
        params.append(asset_id)
    if state is not None:
        clauses.append("state=%s")
        params.append(state)
    params.append(limit)
    with connection() as conn:
        return conn.execute("SELECT * FROM alerts WHERE " + " AND ".join(clauses) + " ORDER BY ts DESC LIMIT %s",
                            params).fetchall()


@app.post("/v1/alerts/{alert_id}/acknowledge", dependencies=[Depends(authenticate)])
def acknowledge(alert_id: uuid.UUID):
    with connection() as conn:
        row = conn.execute("""UPDATE alerts SET state='acknowledged', acknowledged_at=now()
                              WHERE id=%s AND state='open' RETURNING *""", (alert_id,)).fetchone()
        if not row:
            row = conn.execute("SELECT * FROM alerts WHERE id=%s", (alert_id,)).fetchone()
        if not row:
            raise HTTPException(404, "alert not found")
    return row


@app.post("/v1/knowledge/facts", dependencies=[Depends(authenticate)], status_code=201)
def create_fact(payload: FactIn):
    # Site facts remain explicitly operator-supplied; the API does not verify a drawing.
    try:
        return {"id": add_fact(payload.model_dump())}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/v1/knowledge/facts", dependencies=[Depends(authenticate)])
def list_facts(scope: str | None = None):
    if scope not in (None, "reference", "site_supplied"):
        raise HTTPException(422, "invalid scope")
    return facts(scope)


@app.post("/v1/assets/{asset_id}/reports", dependencies=[Depends(authenticate)], status_code=201)
def create_report(asset_id: str, days: int = Query(default=7, ge=1, le=90)):
    with connection() as conn:
        asset = get_asset(conn, asset_id)
        latest = conn.execute("SELECT max(ts) AS ts FROM telemetry WHERE asset_id=%s", (asset_id,)).fetchone()["ts"]
        if latest is None:
            raise HTTPException(422, "no telemetry")
        rows = conn.execute("""SELECT ts,value,quality FROM telemetry WHERE asset_id=%s
                               AND ts >= %s AND ts <= %s ORDER BY ts""",
                            (asset_id, latest - timedelta(days=days), latest)).fetchall()
        alerts = conn.execute("""SELECT state FROM alerts WHERE asset_id=%s AND ts >= %s AND ts <= %s""",
                              (asset_id, latest - timedelta(days=days), latest)).fetchall()
    try:
        with connection() as conn:
            history = conn.execute("""SELECT ts,value FROM telemetry WHERE asset_id=%s AND quality='measured'
                                    ORDER BY ts DESC LIMIT 2880""", (asset_id,)).fetchall()
        fc = forecast(list(reversed(history)), 8, asset["cadence_seconds"], asset_id, "local")
    except ValueError:
        fc = None
    report = build_report(asset, rows, alerts, facts(), fc)
    report_id = uuid.uuid4()
    with connection() as conn:
        conn.execute("INSERT INTO reports(id,asset_id,payload) VALUES (%s,%s,%s)",
                     (report_id, asset_id, Jsonb(report)))
    return {"id": str(report_id), **report}


@app.get("/v1/reports/{report_id}", dependencies=[Depends(authenticate)])
def get_report(report_id: uuid.UUID):
    with connection() as conn:
        row = conn.execute("SELECT id,asset_id,payload,created_at FROM reports WHERE id=%s", (report_id,)).fetchone()
    if not row:
        raise HTTPException(404, "report not found")
    return row

