"""Ventanas de registro. Es la única fuente de verdad del horario:
el móvil solo registra dentro de las ventanas que le devuelve el servidor,
y el servidor descarta cualquier muestra que caiga fuera."""
from datetime import date, datetime, timedelta
import sqlite3

from . import config

Window = tuple[datetime, datetime]


def day_windows(d: date) -> list[Window]:
    """Ventanas de la plantilla semanal para un día, sin festivos ni ausencias."""
    return [
        (datetime.combine(d, a, tzinfo=config.TZ), datetime.combine(d, b, tzinfo=config.TZ))
        for a, b in config.WEEKLY_SCHEDULE.get(d.weekday(), [])
    ]


def holidays(conn: sqlite3.Connection) -> set[str]:
    return {r["day"] for r in conn.execute("SELECT day FROM holidays")}


def is_absent(conn: sqlite3.Connection, agent_id: int, d: date) -> bool:
    iso = d.isoformat()
    return conn.execute(
        "SELECT 1 FROM absences WHERE agent_id=? AND start_day<=? AND end_day>=?",
        (agent_id, iso, iso),
    ).fetchone() is not None


def agent_can_record(conn: sqlite3.Connection, agent_id: int, d: date) -> bool:
    """Sin acuse firmado, de baja o antes de la fecha del acuse: nada se registra."""
    a = conn.execute("SELECT informed_on, active FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not a or not a["active"] or not a["informed_on"]:
        return False
    return d.isoformat() >= a["informed_on"]


def agent_windows(conn: sqlite3.Connection, agent_id: int, d: date, _hol: set[str] | None = None) -> list[Window]:
    hol = holidays(conn) if _hol is None else _hol
    if d.isoformat() in hol or not agent_can_record(conn, agent_id, d) or is_absent(conn, agent_id, d):
        return []
    return day_windows(d)


def agent_windows_range(conn: sqlite3.Connection, agent_id: int, start: date, days: int) -> list[Window]:
    hol = holidays(conn)
    out: list[Window] = []
    for i in range(days):
        out.extend(agent_windows(conn, agent_id, start + timedelta(days=i), hol))
    return out


def in_windows(ts: int, windows: list[Window], tolerance_s: int = 0) -> bool:
    return any(a.timestamp() - tolerance_s <= ts < b.timestamp() + tolerance_s for a, b in windows)


def local_day(ts: int) -> date:
    return datetime.fromtimestamp(ts, config.TZ).date()
