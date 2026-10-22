"""Gestión por línea de comandos.

  python -m app.manage alta --nombre "Justo" --equipo "Ángel" --min-dia 3h --min-semana 15h
  python -m app.manage informado 1 --fecha 2026-10-26     ← tras archivar el acuse firmado
  python -m app.manage zona 1 zonas/justo.geojson --desde 2026-10-26
  python -m app.manage festivo 2026-12-08 "Inmaculada Concepción"
  python -m app.manage ausencia 1 2026-11-10 2026-11-12 "Vacaciones"
  python -m app.manage listar
  python -m app.manage metricas --desde 2026-10-26 --hasta 2026-11-22
  python -m app.manage purgar
  python -m app.manage fin-piloto --anonimizar
"""
import argparse
import json
import re
import secrets
import sys
import time
from datetime import date, timedelta

from . import config, db, geo, schedule
from .api import token_hash
from .totals import recompute, totals_range


def parse_minutes(s: str | None) -> int | None:
    if not s:
        return None
    m = re.fullmatch(r"\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*", s)
    if not m or not (m.group(1) or m.group(2)):
        raise SystemExit(f"Duración no válida: {s!r} (usa 3h, 90m, 2h30m)")
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def new_token() -> str:
    return secrets.token_urlsafe(24)


def cmd_alta(conn, a):
    token, code = new_token(), secrets.token_hex(4).upper()
    cur = conn.execute(
        "INSERT INTO agents (name, team, token_hash, link_code, min_daily_minutes, min_weekly_minutes, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (a.nombre, a.equipo, token_hash(token), code,
         parse_minutes(a.min_dia), parse_minutes(a.min_semana), int(time.time())),
    )
    db.audit(conn, "cli", "alta", f"agent={cur.lastrowid}")
    print(f"Alta de {a.nombre} con id {cur.lastrowid}.")
    print(f"  Token del móvil (se muestra una sola vez): {token}")
    print(f"  Código para vincular Telegram: /start {code}")
    print("  No registrará nada hasta: zona asignada + 'informado' con la fecha del acuse firmado.")


def cmd_token(conn, a):
    token = new_token()
    conn.execute("UPDATE agents SET token_hash=? WHERE id=?", (token_hash(token), a.id))
    db.audit(conn, "cli", "token", f"agent={a.id}")
    print(f"Nuevo token (el anterior deja de funcionar): {token}")


def cmd_informado(conn, a):
    conn.execute("UPDATE agents SET informed_on=? WHERE id=?", (a.fecha, a.id))
    db.audit(conn, "cli", "informado", f"agent={a.id} fecha={a.fecha}")
    print(f"Acuse registrado: el comercial {a.id} registra desde el {a.fecha}.")


def cmd_minimos(conn, a):
    conn.execute("UPDATE agents SET min_daily_minutes=?, min_weekly_minutes=? WHERE id=?",
                 (parse_minutes(a.min_dia), parse_minutes(a.min_semana), a.id))
    db.audit(conn, "cli", "minimos", f"agent={a.id} {a.min_dia}/{a.min_semana}")
    print("Mínimos actualizados.")


def cmd_zona(conn, a):
    with open(a.fichero, encoding="utf-8") as f:
        raw = f.read()
    polys = geo.parse_zones(raw)
    desde = a.desde or date.today().isoformat()
    if desde < date.today().isoformat():
        print("Aviso: la política exige comunicar el cambio de zona ANTES de aplicarlo.", file=sys.stderr)
    conn.execute("INSERT INTO zones (agent_id, name, geojson, valid_from, created_at) VALUES (?,?,?,?,?)",
                 (a.id, a.nombre, json.dumps(json.loads(raw)), desde, int(time.time())))
    db.audit(conn, "cli", "zona", f"agent={a.id} desde={desde} poligonos={len(polys)}")
    print(f"Zona cargada ({len(polys)} polígono(s)), vigente desde {desde}.")


def cmd_festivo(conn, a):
    conn.execute("INSERT OR REPLACE INTO holidays (day, name) VALUES (?,?)", (a.dia, a.nombre))
    print(f"Festivo {a.dia} añadido.")


def cmd_festivos(conn, a):
    n = 0
    with open(a.fichero, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            day, _, name = line.partition(" ")
            date.fromisoformat(day)
            conn.execute("INSERT OR REPLACE INTO holidays (day, name) VALUES (?,?)", (day, name.strip()))
            n += 1
    print(f"{n} festivos cargados.")


def cmd_ausencia(conn, a):
    conn.execute("INSERT INTO absences (agent_id, start_day, end_day, reason) VALUES (?,?,?,?)",
                 (a.id, a.desde, a.hasta, a.motivo))
    db.audit(conn, "cli", "ausencia", f"agent={a.id} {a.desde}..{a.hasta}")
    print("Ausencia registrada: esos días no se toma ninguna posición.")


def cmd_baja(conn, a):
    conn.execute("UPDATE agents SET active=0, token_hash=NULL WHERE id=?", (a.id,))
    db.audit(conn, "cli", "baja", f"agent={a.id}")
    print("Comercial dado de baja; su móvil deja de poder enviar datos.")


def cmd_listar(conn, a):
    for r in conn.execute("SELECT * FROM agents ORDER BY id"):
        z = conn.execute("SELECT valid_from FROM zones WHERE agent_id=? ORDER BY valid_from DESC LIMIT 1",
                         (r["id"],)).fetchone()
        print(f"{r['id']:>3}  {r['name']:<22} {r['team'] or '':<10} "
              f"{'activo' if r['active'] else 'baja':<7} acuse={r['informed_on'] or 'NO':<10} "
              f"zona={'desde ' + z['valid_from'] if z else 'NO':<16} "
              f"telegram={'sí' if r['telegram_id'] else 'no'} "
              f"mín={r['min_daily_minutes'] or '-'}/{r['min_weekly_minutes'] or '-'} min")


def cmd_recalcular(conn, a):
    n = recompute(conn, date.fromisoformat(a.desde), date.fromisoformat(a.hasta))
    print(f"{n} días-comercial recalculados.")


def purge(conn) -> int:
    cutoff = int(time.time()) - config.RETENTION_DAYS * 86400
    n = conn.execute("DELETE FROM positions WHERE ts < ?", (cutoff,)).rowcount
    db.audit(conn, "sistema", "purga", f"posiciones={n}")
    return n


def cmd_purgar(conn, a):
    print(f"{purge(conn)} posiciones de más de {config.RETENTION_DAYS} días borradas.")


def _day_ts(d: date) -> int:
    from datetime import datetime
    return int(datetime.combine(d, datetime.min.time(), config.TZ).timestamp())


def pilot_metrics(conn, start: date, end: date) -> str:
    """Tabla de evaluación del piloto (sección «Qué se evalúa»)."""
    out = [f"Métricas del piloto {start} → {end}", ""]
    for r in conn.execute("SELECT * FROM agents WHERE informed_on IS NOT NULL ORDER BY id"):
        t = totals_range(conn, r["id"], start, end, live_today=False)
        disc = conn.execute("SELECT COALESCE(SUM(count),0) FROM discarded_samples WHERE agent_id=? AND day BETWEEN ? AND ?",
                            (r["id"], start.isoformat(), end.isoformat())).fetchone()[0]
        j = conn.execute(
            "SELECT COUNT(*), AVG(decided_at - created_at), "
            "SUM(CASE WHEN status='aceptada' THEN 1 ELSE 0 END) FROM justifications "
            "WHERE agent_id=? AND start_ts >= ? AND start_ts < ?",
            (r["id"], _day_ts(start), _day_ts(end + timedelta(days=1))),
        ).fetchone()
        nodata_pct = 100 * t.no_data_total / t.scheduled_min if t.scheduled_min else 0
        out.append(f"{r['name']}")
        out.append(f"  Jornada medida: {t.scheduled_min} min")
        out.append(f"  Fiabilidad: {nodata_pct:.1f} % sin datos "
                   f"(GPS apagado {t.gps_off_min} min) → umbral < 10 %: {'OK' if nodata_pct < 10 else 'NO'}")
        out.append(f"  Respeto del horario: {disc} muestras recibidas fuera de jornada y descartadas sin guardar "
                   f"→ {'OK' if disc == 0 else 'REVISAR la app'}")
        out.append(f"  Ubicación simulada: {t.mock_min} min")
        out.append(f"  Incidencias: {j[0]} justificaciones, {j[2] or 0} aceptadas, "
                   f"tiempo medio de decisión {((j[1] or 0) / 3600):.1f} h → umbral < 48 h")
        out.append("")
    stored = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    out.append(f"Posiciones almacenadas ahora mismo: {stored} (todas dentro de jornada por construcción).")
    out.append("Batería, precisión (salidas falsas) y percepción: se recogen en la entrevista con cada participante.")
    return "\n".join(out)


def cmd_metricas(conn, a):
    print(pilot_metrics(conn, date.fromisoformat(a.desde), date.fromisoformat(a.hasta)))


def cmd_fin_piloto(conn, a):
    if not a.si:
        raise SystemExit("Borra TODAS las posiciones y totales del piloto. Repite con --si para confirmar.")
    if a.anonimizar:
        rows = conn.execute("SELECT SUM(scheduled_min), SUM(inside_min), SUM(outside_min), SUM(justified_min), "
                            "SUM(nodata_min), SUM(gps_off_min), SUM(mock_min) FROM daily_totals").fetchone()
        with open("totales_anonimos_piloto.json", "w", encoding="utf-8") as f:
            json.dump(dict(zip(["jornada", "dentro", "fuera", "justificado", "sin_datos", "gps_apagado", "simulada"],
                               rows)), f, ensure_ascii=False, indent=2)
        print("Totales agregados (sin nombres) guardados en totales_anonimos_piloto.json")
    for t in ("positions", "daily_totals", "justifications", "discarded_samples"):
        conn.execute(f"DELETE FROM {t}")
    db.audit(conn, "cli", "fin-piloto", f"anonimizar={a.anonimizar}")
    print("Datos del piloto borrados.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="manage")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("alta"); s.add_argument("--nombre", required=True); s.add_argument("--equipo")
    s.add_argument("--min-dia"); s.add_argument("--min-semana"); s.set_defaults(f=cmd_alta)
    s = sp.add_parser("token"); s.add_argument("id", type=int); s.set_defaults(f=cmd_token)
    s = sp.add_parser("informado"); s.add_argument("id", type=int)
    s.add_argument("--fecha", required=True, type=lambda v: date.fromisoformat(v).isoformat()); s.set_defaults(f=cmd_informado)
    s = sp.add_parser("minimos"); s.add_argument("id", type=int); s.add_argument("--min-dia")
    s.add_argument("--min-semana"); s.set_defaults(f=cmd_minimos)
    s = sp.add_parser("zona"); s.add_argument("id", type=int); s.add_argument("fichero")
    s.add_argument("--desde", type=lambda v: date.fromisoformat(v).isoformat()); s.add_argument("--nombre")
    s.set_defaults(f=cmd_zona)
    s = sp.add_parser("festivo"); s.add_argument("dia", type=lambda v: date.fromisoformat(v).isoformat())
    s.add_argument("nombre", nargs="?", default=""); s.set_defaults(f=cmd_festivo)
    s = sp.add_parser("festivos"); s.add_argument("fichero"); s.set_defaults(f=cmd_festivos)
    s = sp.add_parser("ausencia"); s.add_argument("id", type=int)
    s.add_argument("desde", type=lambda v: date.fromisoformat(v).isoformat())
    s.add_argument("hasta", type=lambda v: date.fromisoformat(v).isoformat())
    s.add_argument("motivo", nargs="?", default=""); s.set_defaults(f=cmd_ausencia)
    s = sp.add_parser("baja"); s.add_argument("id", type=int); s.set_defaults(f=cmd_baja)
    s = sp.add_parser("listar"); s.set_defaults(f=cmd_listar)
    s = sp.add_parser("recalcular"); s.add_argument("--desde", required=True); s.add_argument("--hasta", required=True)
    s.set_defaults(f=cmd_recalcular)
    s = sp.add_parser("purgar"); s.set_defaults(f=cmd_purgar)
    s = sp.add_parser("metricas"); s.add_argument("--desde", required=True); s.add_argument("--hasta", required=True)
    s.set_defaults(f=cmd_metricas)
    s = sp.add_parser("fin-piloto"); s.add_argument("--anonimizar", action="store_true")
    s.add_argument("--si", action="store_true"); s.set_defaults(f=cmd_fin_piloto)

    a = p.parse_args(argv)
    with db.session() as conn:
        a.f(conn, a)


if __name__ == "__main__":
    main()
