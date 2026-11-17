"""Ventanas de registro. Es la única fuente de verdad del horario:
el móvil solo registra dentro de las ventanas que le devuelve el servidor,
y el servidor descarta cualquier muestra que caiga fuera."""
import re
from datetime import date, datetime, time, timedelta
import sqlite3

from . import config

Window = tuple[datetime, datetime]
Weekly = dict[int, list[tuple[time, time]]]

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_schedules (
    id INTEGER PRIMARY KEY,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    valid_from TEXT NOT NULL,
    spec TEXT,                 -- NULL = vuelve al horario general
    created_at INTEGER NOT NULL
);
"""

DAYS = "LMXJVSD"   # lunes … domingo


# ───────── horario como texto: "L-V 11:00-19:00; S 10:30-14:00" ─────────

def parse_spec(spec: str) -> Weekly:
    """Grupos separados por ';'. Cada grupo: días (L-V, L,X,V, S…) y uno o más tramos HH:MM-HH:MM."""
    out: Weekly = {}
    groups = [g.strip() for g in spec.replace("\n", ";").split(";") if g.strip()]
    if not groups:
        raise ValueError("Horario vacío")
    for g in groups:
        m = re.fullmatch(r"([LMXJVSD,\- ]+?)\s+((?:\d{1,2}[:.]\d{2}\s*-\s*\d{1,2}[:.]\d{2}[\s,y]*)+)", g.upper())
        if not m:
            raise ValueError(f"No entiendo «{g}». Ejemplo: L-V 11:00-19:00; S 10:30-14:00")
        days: set[int] = set()
        for part in m.group(1).replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-")
                if a not in DAYS or b not in DAYS or DAYS.index(a) > DAYS.index(b):
                    raise ValueError(f"Rango de días no válido: {part}")
                days.update(range(DAYS.index(a), DAYS.index(b) + 1))
            elif part in DAYS:
                days.add(DAYS.index(part))
            else:
                raise ValueError(f"Día no válido: {part} (usa L M X J V S D)")
        for h1, m1, h2, m2 in re.findall(r"(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})", m.group(2)):
            a, b = time(int(h1), int(m1)), time(int(h2), int(m2))
            if b <= a:
                raise ValueError(f"Tramo no válido: {h1}:{m1}-{h2}:{m2}")
            for d in days:
                out.setdefault(d, []).append((a, b))
    for d, tr in out.items():
        tr.sort()
        for (a1, b1), (a2, b2) in zip(tr, tr[1:]):
            if a2 < b1:
                raise ValueError(f"Tramos solapados el {DAYS[d]}")
    return out


def format_weekly(w: Weekly) -> str:
    """Texto legible agrupando días consecutivos con los mismos tramos."""
    key = lambda d: " y ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in w.get(d, []))
    parts, d = [], 0
    while d < 7:
        if not w.get(d):
            d += 1
            continue
        e = d
        while e + 1 < 7 and key(e + 1) == key(d):
            e += 1
        parts.append(f"{DAYS[d]}{'-' + DAYS[e] if e > d else ''} {key(d)}")
        d = e + 1
    return "; ".join(parts) or "sin jornada"


GENERAL = config.WEEKLY_SCHEDULE


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def agent_weekly(conn: sqlite3.Connection, agent_id: int, d: date) -> tuple[Weekly, bool]:
    """Plantilla semanal del comercial vigente el día d, y si es la general."""
    r = conn.execute(
        "SELECT spec FROM agent_schedules WHERE agent_id=? AND valid_from<=? ORDER BY valid_from DESC, id DESC LIMIT 1",
        (agent_id, d.isoformat())).fetchone()
    if r is None or r["spec"] is None:
        return GENERAL, True
    return parse_spec(r["spec"]), False


def day_windows(d: date, weekly: Weekly | None = None) -> list[Window]:
    """Ventanas de una plantilla semanal para un día, sin festivos ni ausencias."""
    return [
        (datetime.combine(d, a, tzinfo=config.TZ), datetime.combine(d, b, tzinfo=config.TZ))
        for a, b in (weekly or GENERAL).get(d.weekday(), [])
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
    return day_windows(d, agent_weekly(conn, agent_id, d)[0])


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
