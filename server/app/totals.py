"""Clasificación minuto a minuto de la jornada y totales diarios.

Cada minuto de jornada acaba en exactamente una categoría:
  justified  → cubierto por una justificación aceptada
  mock       → la muestra del minuto viene de una ubicación simulada
  gps_off    → localización desactivada o permiso retirado
  nodata     → sin muestra reciente, o precisión peor que ACCURACY_MAX_M
  inside / outside
"""
import time
from bisect import bisect_right
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
import sqlite3

from . import config, geo, schedule, zones


@dataclass
class DayTotals:
    agent_id: int
    day: str
    scheduled_min: int = 0
    inside_min: int = 0
    outside_min: int = 0
    justified_min: int = 0
    nodata_min: int = 0
    gps_off_min: int = 0
    mock_min: int = 0
    zone_missing: int = 0

    def add(self, other: "DayTotals") -> None:
        for k in ("scheduled_min", "inside_min", "outside_min", "justified_min",
                  "nodata_min", "gps_off_min", "mock_min", "zone_missing"):
            setattr(self, k, getattr(self, k) + getattr(other, k))

    @property
    def no_data_total(self) -> int:
        return self.nodata_min + self.gps_off_min


def zones_for_day(conn: sqlite3.Connection, agent_id: int, d: date):
    return zones.polygons_for_day(conn, agent_id, d)


def classify_day(conn: sqlite3.Connection, agent_id: int, d: date, until_ts: int | None = None) -> DayTotals:
    """until_ts: para el día en curso, solo cuenta minutos ya transcurridos."""
    res = DayTotals(agent_id, d.isoformat())
    windows = schedule.agent_windows(conn, agent_id, d)
    if not windows:
        return res
    polys = zones_for_day(conn, agent_id, d)
    if polys is None:
        res.zone_missing = 1

    day_start = int(windows[0][0].timestamp()) - config.SAMPLE_CARRY_S
    day_end = int(windows[-1][1].timestamp())
    samples = conn.execute(
        "SELECT ts, lat, lon, accuracy, status FROM positions WHERE agent_id=? AND ts>=? AND ts<? ORDER BY ts",
        (agent_id, day_start, day_end),
    ).fetchall()
    sample_ts = [s["ts"] for s in samples]

    justs = conn.execute(
        "SELECT start_ts, end_ts FROM justifications WHERE agent_id=? AND status='aceptada' AND end_ts>? AND start_ts<?",
        (agent_id, day_start, day_end),
    ).fetchall()

    for w_start, w_end in windows:
        m = int(w_start.timestamp())
        end = int(w_end.timestamp())
        while m < end:
            if until_ts is not None and m >= until_ts:
                return res
            res.scheduled_min += 1
            cat = _classify_minute(m, samples, sample_ts, justs, polys)
            setattr(res, f"{cat}_min", getattr(res, f"{cat}_min") + 1)
            m += 60
    return res


def _classify_minute(m, samples, sample_ts, justs, polys) -> str:
    mid = m + 30
    if any(j["start_ts"] <= mid < j["end_ts"] for j in justs):
        return "justified"
    # Muestras dentro del minuto [m, m+60): si alguna es simulada, manda.
    lo = bisect_right(sample_ts, m - 1)
    hi = bisect_right(sample_ts, m + 59)
    in_minute = samples[lo:hi]
    if any(s["status"] == "mock" for s in in_minute):
        return "mock"
    if in_minute:
        s = in_minute[-1]
    else:
        # Arrastre de la última muestra anterior si es reciente.
        if lo == 0 or m - sample_ts[lo - 1] > config.SAMPLE_CARRY_S:
            return "nodata"
        s = samples[lo - 1]
    if s["status"] in ("gps_off", "no_permission"):
        return "gps_off"
    if s["status"] == "mock":
        return "mock"
    if s["lat"] is None or s["lon"] is None:
        return "nodata"
    acc = s["accuracy"] if s["accuracy"] is not None else config.ACCURACY_MAX_M
    if acc > config.ACCURACY_MAX_M or polys is None:
        return "nodata"
    tol = min(acc, config.BOUNDARY_TOLERANCE_MAX_M)
    return "inside" if geo.inside_with_tolerance(s["lon"], s["lat"], polys, tol) else "outside"


def store_day(conn: sqlite3.Connection, t: DayTotals) -> None:
    d = asdict(t)
    d["computed_at"] = int(time.time())
    conn.execute(
        """INSERT INTO daily_totals (agent_id, day, scheduled_min, inside_min, outside_min, justified_min,
               nodata_min, gps_off_min, mock_min, zone_missing, computed_at)
           VALUES (:agent_id,:day,:scheduled_min,:inside_min,:outside_min,:justified_min,
               :nodata_min,:gps_off_min,:mock_min,:zone_missing,:computed_at)
           ON CONFLICT(agent_id, day) DO UPDATE SET
               scheduled_min=excluded.scheduled_min, inside_min=excluded.inside_min,
               outside_min=excluded.outside_min, justified_min=excluded.justified_min,
               nodata_min=excluded.nodata_min, gps_off_min=excluded.gps_off_min,
               mock_min=excluded.mock_min, zone_missing=excluded.zone_missing,
               computed_at=excluded.computed_at""",
        d,
    )


def recompute(conn: sqlite3.Connection, start: date, end: date, agent_id: int | None = None) -> int:
    """Recalcula y guarda [start, end]. Días ya sin posiciones (purgadas) no se tocan,
    para no machacar totales buenos con ceros."""
    ids = [agent_id] if agent_id else [r["id"] for r in conn.execute("SELECT id FROM agents")]
    oldest_kept = int(time.time()) - config.RETENTION_DAYS * 86400
    n = 0
    d = start
    while d <= end:
        day_end_ts = datetime.combine(d + timedelta(days=1), datetime.min.time(), config.TZ).timestamp()
        if day_end_ts > oldest_kept:
            for a in ids:
                store_day(conn, classify_day(conn, a, d))
                n += 1
        d += timedelta(days=1)
    return n


def totals_range(conn: sqlite3.Connection, agent_id: int, start: date, end: date,
                 live_today: bool = True) -> DayTotals:
    """Suma de totales guardados; el día de hoy se calcula al vuelo hasta ahora."""
    agg = DayTotals(agent_id, f"{start.isoformat()}..{end.isoformat()}")
    now = int(time.time())
    today = schedule.local_day(now)
    for r in conn.execute(
        "SELECT * FROM daily_totals WHERE agent_id=? AND day>=? AND day<=? AND day<>?",
        (agent_id, start.isoformat(), end.isoformat(), today.isoformat() if live_today else ""),
    ):
        agg.add(DayTotals(**{k: r[k] for k in DayTotals.__dataclass_fields__}))
    if live_today and start <= today <= end:
        agg.add(classify_day(conn, agent_id, today, until_ts=now))
    return agg
