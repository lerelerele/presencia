"""Textos de informe. Solo totales de tiempo: nunca coordenadas, rutas ni lugares."""
from datetime import date, timedelta
import sqlite3

from . import config
from .totals import DayTotals, totals_range

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def hm(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} h {m:02d} min"
    return f"{h} h" if h else f"{m} min"


def pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)} %" if whole else "—"


def week_bounds(d: date) -> tuple[date, date]:
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def month_bounds(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt - timedelta(days=1)


def fmt_day(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def period_label(kind: str, start: date, end: date) -> str:
    if kind == "dia":
        return fmt_day(start)
    if kind == "semana":
        return f"semana del {start.day}/{start.month} al {end.day}/{end.month}"
    return f"{MESES[start.month - 1]} {start.year}"


def minimum_for(agent: sqlite3.Row, kind: str, t: DayTotals, start: date, end: date) -> int | None:
    if kind == "dia":
        return agent["min_daily_minutes"] if t.scheduled_min else None
    if kind == "semana":
        return agent["min_weekly_minutes"]
    return None  # el mes no tiene mínimo propio; se ven las semanas


def agent_block(agent: sqlite3.Row, t: DayTotals, kind: str, start: date, end: date) -> str:
    lines = [f"<b>{agent['name']}</b>" + (f" · {agent['team']}" if agent["team"] else "")]
    if t.scheduled_min == 0:
        lines.append("  Sin jornada registrable (festivo, ausencia o sin acuse).")
        return "\n".join(lines)
    lines.append(f"  Jornada: {hm(t.scheduled_min)}")
    lines.append(f"  ✅ Dentro de zona: {hm(t.inside_min)} ({pct(t.inside_min, t.scheduled_min)})")
    lines.append(f"  ↗️ Fuera de zona: {hm(t.outside_min)}")
    if t.justified_min:
        lines.append(f"  📝 Fuera justificado: {hm(t.justified_min)}")
    lines.append(f"  ❔ Sin datos: {hm(t.no_data_total)}"
                 + (f" (GPS apagado {hm(t.gps_off_min)})" if t.gps_off_min else ""))
    if t.mock_min:
        lines.append(f"  ⚠️ Ubicación simulada: {hm(t.mock_min)}")
    if t.zone_missing:
        lines.append("  ⚠️ Sin zona asignada en algún día: no se pudo medir.")
    minimum = minimum_for(agent, kind, t, start, end)
    if minimum:
        counted = t.inside_min + t.justified_min
        ok = "cumple" if counted >= minimum else f"faltan {hm(minimum - counted)}"
        lines.append(f"  Mínimo {hm(minimum)}: {ok}")
    return "\n".join(lines)


def build(conn: sqlite3.Connection, kind: str, ref: date, agent_id: int | None = None) -> str:
    if kind == "dia":
        start = end = ref
    elif kind == "semana":
        start, end = week_bounds(ref)
    else:
        start, end = month_bounds(ref)
    q = "SELECT * FROM agents WHERE active=1"
    args: tuple = ()
    if agent_id:
        q, args = "SELECT * FROM agents WHERE id=?", (agent_id,)
    agents = conn.execute(q + " ORDER BY team, name", args).fetchall()

    title = {"dia": "Informe diario", "semana": "Informe semanal", "mes": "Informe mensual"}[kind]
    head = [f"<b>{title}</b> — {period_label(kind, start, end)}"]
    if config.PILOT:
        head.append(config.PILOT_BANNER)
    blocks = [agent_block(a, totals_range(conn, a["id"], start, end), kind, start, end) for a in agents]
    pend = conn.execute(
        "SELECT COUNT(*) FROM justifications WHERE status='pendiente'" + (" AND agent_id=?" if agent_id else ""),
        (agent_id,) if agent_id else (),
    ).fetchone()[0]
    tail = [f"Justificaciones pendientes de revisar: {pend}"] if pend else []
    return "\n".join(head) + "\n\n" + "\n\n".join(blocks or ["(sin comerciales activos)"]) + (
        "\n\n" + "\n".join(tail) if tail else "")
