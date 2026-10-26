"""Zonas con nombre y asignaciones con fechas.

- Una zona es una pieza del mapa con nombre único («Centro», «Ensanche»…).
  Su forma puede cambiar con el tiempo: cada cambio es una versión con fecha de vigencia.
- Una asignación une comercial y zona durante [desde, hasta). `hasta` vacío = indefinida.
- Un comercial puede tener 0, 1 o varias zonas a la vez; una zona puede pasar de uno a otro.
- Para medir un día se usa la unión de todas las zonas asignadas ese día, cada una
  con la forma vigente ese día.

Fechas: `desde` incluido, `hasta` excluido (es el primer día en que ya no aplica).
"""
import json
import re
import sqlite3
import time
from datetime import date

from . import geo

SCHEMA = """
CREATE TABLE IF NOT EXISTS zone_defs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS zone_versions (
    id INTEGER PRIMARY KEY,
    zone_id INTEGER NOT NULL REFERENCES zone_defs(id),
    geojson TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS zone_assignments (
    id INTEGER PRIMARY KEY,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    zone_id INTEGER NOT NULL REFERENCES zone_defs(id),
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assign_agent ON zone_assignments(agent_id, valid_from);
"""


class ZoneError(ValueError):
    pass


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate_legacy(conn)


def _migrate_legacy(conn: sqlite3.Connection) -> None:
    """La versión anterior guardaba por comercial un «paquete» completo de zonas en `zones`,
    vigente desde una fecha hasta el siguiente paquete. Cada paquete pasa a ser una zona
    con nombre, asignada a ese comercial durante el mismo periodo. Se hace una sola vez."""
    probe = "SELECT 1 FROM sqlite_master WHERE type='table' AND name='zones'"
    if not conn.execute(probe).fetchone():
        return
    # API y bot arrancan a la vez: bloqueo de escritura y volver a mirar, para migrar una sola vez.
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    if not conn.execute(probe).fetchone():
        conn.rollback()
        return
    rows = conn.execute("SELECT * FROM zones ORDER BY agent_id, valid_from, id").fetchall()
    now = int(time.time())
    by_agent: dict[int, list] = {}
    for r in rows:
        by_agent.setdefault(r["agent_id"], []).append(r)
    for agent_id, lst in by_agent.items():
        for i, r in enumerate(lst):
            base = (r["name"] or f"Zona comercial {agent_id}").strip()
            name, n = base, 2
            while conn.execute("SELECT 1 FROM zone_defs WHERE name=?", (name,)).fetchone():
                name, n = f"{base} ({n})", n + 1
            zid = conn.execute("INSERT INTO zone_defs (name, created_at) VALUES (?,?)", (name, now)).lastrowid
            conn.execute("INSERT INTO zone_versions (zone_id, geojson, valid_from, created_at) VALUES (?,?,?,?)",
                         (zid, r["geojson"], r["valid_from"], now))
            valid_to = lst[i + 1]["valid_from"] if i + 1 < len(lst) else None
            if valid_to == r["valid_from"]:
                continue  # paquete sustituido el mismo día: nunca estuvo vigente
            conn.execute("INSERT INTO zone_assignments (agent_id, zone_id, valid_from, valid_to, created_at) "
                         "VALUES (?,?,?,?,?)", (agent_id, zid, r["valid_from"], valid_to, now))
    conn.execute("ALTER TABLE zones RENAME TO zones_legacy_migrated")
    conn.commit()


# ───────── consultas ─────────

def get_zone(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    z = conn.execute("SELECT * FROM zone_defs WHERE name=?", (name.strip(),)).fetchone()
    if not z:
        raise ZoneError(f"No existe la zona «{name}». Usa `zonas` para ver las que hay.")
    return z


def geometry_on(conn: sqlite3.Connection, zone_id: int, d: date):
    row = conn.execute(
        "SELECT geojson FROM zone_versions WHERE zone_id=? AND valid_from<=? "
        "ORDER BY valid_from DESC, id DESC LIMIT 1", (zone_id, d.isoformat())).fetchone()
    return geo.parse_zones(row["geojson"]) if row else None


def assigned_zone_ids(conn: sqlite3.Connection, agent_id: int, d: date) -> list[int]:
    iso = d.isoformat()
    return [r["zone_id"] for r in conn.execute(
        "SELECT DISTINCT zone_id FROM zone_assignments WHERE agent_id=? AND valid_from<=? "
        "AND (valid_to IS NULL OR valid_to>?) ORDER BY zone_id", (agent_id, iso, iso))]


def polygons_for_day(conn: sqlite3.Connection, agent_id: int, d: date):
    """Unión de los polígonos de todas las zonas asignadas ese día. None si no tiene ninguna."""
    polys = []
    for zid in assigned_zone_ids(conn, agent_id, d):
        g = geometry_on(conn, zid, d)
        if g:
            polys.extend(g)
    return polys or None


def has_any_assignment(conn: sqlite3.Connection, agent_id: int) -> bool:
    return conn.execute("SELECT 1 FROM zone_assignments WHERE agent_id=? LIMIT 1", (agent_id,)).fetchone() is not None


def agent_zone_names(conn: sqlite3.Connection, agent_id: int, d: date) -> list[str]:
    ids = assigned_zone_ids(conn, agent_id, d)
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    return [r["name"] for r in conn.execute(f"SELECT name FROM zone_defs WHERE id IN ({q}) ORDER BY name", ids)]


def holders(conn: sqlite3.Connection, zone_id: int, d: date) -> list[sqlite3.Row]:
    iso = d.isoformat()
    return conn.execute(
        "SELECT a.id, a.name FROM zone_assignments z JOIN agents a ON a.id=z.agent_id "
        "WHERE z.zone_id=? AND z.valid_from<=? AND (z.valid_to IS NULL OR z.valid_to>?) ORDER BY a.name",
        (zone_id, iso, iso)).fetchall()


# ───────── cambios ─────────

def _valid_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 60 or not re.fullmatch(r"[\w .,'()/-]+", name):
        raise ZoneError(f"Nombre de zona no válido: «{name}» (letras, números, espacios y .,'()/- ; máx. 60)")
    return name


def create_zone(conn: sqlite3.Connection, name: str, geojson: str | dict, desde: str) -> int:
    name = _valid_name(name)
    polys = geo.parse_zones(geojson)
    if conn.execute("SELECT 1 FROM zone_defs WHERE name=?", (name,)).fetchone():
        raise ZoneError(f"Ya existe una zona «{name}». Para cambiar su forma usa `zona-forma`.")
    raw = json.dumps(geojson if isinstance(geojson, dict) else json.loads(geojson), separators=(",", ":"))
    now = int(time.time())
    zid = conn.execute("INSERT INTO zone_defs (name, created_at) VALUES (?,?)", (name, now)).lastrowid
    conn.execute("INSERT INTO zone_versions (zone_id, geojson, valid_from, created_at) VALUES (?,?,?,?)",
                 (zid, raw, desde, now))
    return len(polys)


def reshape_zone(conn: sqlite3.Connection, name: str, geojson: str | dict, desde: str) -> int:
    z = get_zone(conn, name)
    polys = geo.parse_zones(geojson)
    raw = json.dumps(geojson if isinstance(geojson, dict) else json.loads(geojson), separators=(",", ":"))
    conn.execute("INSERT INTO zone_versions (zone_id, geojson, valid_from, created_at) VALUES (?,?,?,?)",
                 (z["id"], raw, desde, int(time.time())))
    return len(polys)


def split_features(geojson: str | dict) -> list[tuple[str, dict]]:
    """Un FeatureCollection con varias zonas → [(nombre, Feature)]. Cada Feature necesita
    la propiedad `name` (o `nombre`), que se pone en geojson.io al pinchar el polígono."""
    data = json.loads(geojson) if isinstance(geojson, str) else geojson
    if data.get("type") != "FeatureCollection":
        raise ZoneError("Para importar varias zonas el archivo debe ser un FeatureCollection.")
    out, seen = [], set()
    for i, f in enumerate(data.get("features", []), 1):
        g = (f.get("geometry") or {}).get("type")
        if g not in ("Polygon", "MultiPolygon"):
            raise ZoneError(f"El elemento {i} es un {g or 'vacío'}: solo valen polígonos.")
        props = f.get("properties") or {}
        name = str(props.get("name") or props.get("nombre") or "").strip()
        if not name:
            raise ZoneError(f"El polígono {i} no tiene nombre: en geojson.io pínchalo y añade la propiedad `name`.")
        name = _valid_name(name)
        if name.lower() in seen:
            raise ZoneError(f"Nombre repetido en el archivo: «{name}».")
        seen.add(name.lower())
        geo.parse_zones(f)
        out.append((name, {"type": "FeatureCollection", "features": [f]}))
    if not out:
        raise ZoneError("El archivo no contiene ningún polígono.")
    return out


def _open_assignment(conn, agent_id: int, zone_id: int, on: str):
    return conn.execute(
        "SELECT * FROM zone_assignments WHERE agent_id=? AND zone_id=? AND valid_from<=? "
        "AND (valid_to IS NULL OR valid_to>?) ORDER BY valid_from DESC LIMIT 1",
        (agent_id, zone_id, on, on)).fetchone()


def assign(conn: sqlite3.Connection, agent_id: int, zone_name: str, desde: str, hasta: str | None = None) -> None:
    z = get_zone(conn, zone_name)
    if hasta and hasta <= desde:
        raise ZoneError("`hasta` debe ser posterior a `desde`.")
    if not conn.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone():
        raise ZoneError(f"No existe el comercial {agent_id}.")
    clash = conn.execute(
        "SELECT valid_from, valid_to FROM zone_assignments WHERE agent_id=? AND zone_id=? "
        "AND (valid_to IS NULL OR valid_to>?) AND (? IS NULL OR valid_from<?)",
        (agent_id, z["id"], desde, hasta, hasta)).fetchone()
    if clash:
        raise ZoneError(f"El comercial {agent_id} ya tiene «{z['name']}» desde {clash['valid_from']}"
                        + (f" hasta {clash['valid_to']}" if clash["valid_to"] else "") + ".")
    conn.execute("INSERT INTO zone_assignments (agent_id, zone_id, valid_from, valid_to, created_at) "
                 "VALUES (?,?,?,?,?)", (agent_id, z["id"], desde, hasta, int(time.time())))


def unassign(conn: sqlite3.Connection, agent_id: int, zone_name: str, desde: str) -> None:
    """Desde `desde` (incluido) el comercial deja de tener la zona."""
    z = get_zone(conn, zone_name)
    a = _open_assignment(conn, agent_id, z["id"], desde)
    if a is None:
        raise ZoneError(f"El comercial {agent_id} no tiene «{z['name']}» el {desde}.")
    if a["valid_from"] == desde:
        conn.execute("DELETE FROM zone_assignments WHERE id=?", (a["id"],))
    else:
        conn.execute("UPDATE zone_assignments SET valid_to=? WHERE id=?", (desde, a["id"]))


def transfer(conn: sqlite3.Connection, zone_name: str, from_agent: int, to_agent: int, desde: str) -> None:
    unassign(conn, from_agent, zone_name, desde)
    assign(conn, to_agent, zone_name, desde)


def release_all(conn: sqlite3.Connection, agent_id: int, desde: str) -> list[str]:
    """Baja: el comercial deja todas sus zonas desde `desde`. Devuelve los nombres liberados."""
    freed = set()
    for r in conn.execute(
            "SELECT z.id, z.valid_from, d.name FROM zone_assignments z JOIN zone_defs d ON d.id=z.zone_id "
            "WHERE z.agent_id=? AND (z.valid_to IS NULL OR z.valid_to>?)", (agent_id, desde)).fetchall():
        if r["valid_from"] >= desde:      # asignación que aún no había empezado: se anula
            conn.execute("DELETE FROM zone_assignments WHERE id=?", (r["id"],))
        else:
            conn.execute("UPDATE zone_assignments SET valid_to=? WHERE id=?", (desde, r["id"]))
        freed.add(r["name"])
    return sorted(freed)
