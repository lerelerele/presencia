"""API para la app del móvil de empresa.

POST /api/v1/samples   lote de muestras (solo se guardan las que caen en jornada)
GET  /api/v1/schedule  ventanas de registro de los próximos días
GET  /api/v1/me        totales propios (transparencia: hoy, semana, mes)
"""
import hashlib
import time
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import config, db, reports, schedule, zones
from .totals import totals_range

app = FastAPI(title="Presencia en zona", docs_url=None, redoc_url=None)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_conn():
    conn = db.connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_agent(authorization: str = Header(default=""), conn=Depends(get_conn)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Falta el token del dispositivo")
    row = conn.execute(
        "SELECT * FROM agents WHERE token_hash=? AND active=1", (token_hash(authorization[7:].strip()),)
    ).fetchone()
    if not row:
        raise HTTPException(401, "Token no válido o comercial dado de baja")
    return row


class Sample(BaseModel):
    ts: int = Field(..., description="segundos epoch UTC")
    status: Literal["ok", "gps_off", "no_permission", "mock"]
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    accuracy: float | None = Field(None, ge=0)


class Batch(BaseModel):
    samples: list[Sample] = Field(..., max_length=5000)


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/v1/samples")
def post_samples(batch: Batch, agent=Depends(get_agent), conn=Depends(get_conn)):
    now = int(time.time())
    accepted = discarded = 0
    windows_cache: dict = {}
    discarded_by_day: dict[str, int] = {}
    for s in batch.samples:
        if s.ts > now + 300:
            discarded += 1
            continue
        d = schedule.local_day(s.ts)
        if d not in windows_cache:
            windows_cache[d] = schedule.agent_windows(conn, agent["id"], d)
        if not schedule.in_windows(s.ts, windows_cache[d], config.EDGE_TOLERANCE_S):
            # Fuera de jornada: no se guarda nada de la muestra, solo se cuenta.
            discarded += 1
            discarded_by_day[d.isoformat()] = discarded_by_day.get(d.isoformat(), 0) + 1
            continue
        lat, lon = (s.lat, s.lon) if s.status == "ok" else (None, None)
        cur = conn.execute(
            "INSERT OR IGNORE INTO positions (agent_id, ts, lat, lon, accuracy, status, received_at) VALUES (?,?,?,?,?,?,?)",
            (agent["id"], s.ts, lat, lon, s.accuracy, s.status, now),
        )
        accepted += cur.rowcount
    for day, n in discarded_by_day.items():
        conn.execute(
            "INSERT INTO discarded_samples (agent_id, day, count) VALUES (?,?,?) "
            "ON CONFLICT(agent_id, day) DO UPDATE SET count = count + excluded.count",
            (agent["id"], day, n),
        )
    return {"accepted": accepted, "discarded": discarded}


@app.get("/api/v1/schedule")
def get_schedule(agent=Depends(get_agent), conn=Depends(get_conn)):
    now = int(time.time())
    today = schedule.local_day(now)
    wins = schedule.agent_windows_range(conn, agent["id"], today, config.SCHEDULE_HORIZON_DAYS)
    reason = None
    if not agent["informed_on"]:
        reason = "Pendiente de acuse de recibo: no se registra nada."
    elif not zones.has_any_assignment(conn, agent["id"]):
        reason = "Sin zona asignada todavía."
    return {
        "agent": agent["name"],
        "server_time": now,
        "pilot": config.PILOT,
        "windows": [[int(a.timestamp()), int(b.timestamp())] for a, b in wins if b.timestamp() > now],
        "valid_until": int(now + config.SCHEDULE_HORIZON_DAYS * 86400),
        "notice": reason,
    }


@app.get("/api/v1/me")
def get_me(agent=Depends(get_agent), conn=Depends(get_conn)):
    today = schedule.local_day(int(time.time()))
    out = {"agent": agent["name"], "pilot": config.PILOT, "periods": {}}
    for kind, (a, b) in {
        "hoy": (today, today),
        "semana": reports.week_bounds(today),
        "mes": reports.month_bounds(today),
    }.items():
        t = totals_range(conn, agent["id"], a, min(b, today))
        out["periods"][kind] = {
            "jornada_min": t.scheduled_min, "dentro_min": t.inside_min, "fuera_min": t.outside_min,
            "justificado_min": t.justified_min, "sin_datos_min": t.no_data_total,
            "gps_apagado_min": t.gps_off_min, "simulada_min": t.mock_min,
        }
    out["minimo_diario_min"] = agent["min_daily_minutes"]
    out["minimo_semanal_min"] = agent["min_weekly_minutes"]
    return out
