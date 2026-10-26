"""Varias zonas por comercial, traspasos y migración del formato anterior."""
import json
import sqlite3
from datetime import date, datetime

import pytest

from app import config, db, zones
from app.manage import main as manage_main
from app.totals import classify_day


def square(lon, lat, d=0.004, name=None):
    f = {"type": "Feature", "properties": {"name": name} if name else {},
         "geometry": {"type": "Polygon", "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d],
                                                          [lon, lat + d], [lon, lat]]]}}
    return f


# Tres zonas separadas ~1 km entre sí
A, B, C = square(-3.370, 40.480), square(-3.360, 40.480), square(-3.350, 40.480)
IN_A, IN_B, IN_C = (40.482, -3.368), (40.482, -3.358), (40.482, -3.348)
FC = lambda f: {"type": "FeatureCollection", "features": [f]}
TZ = config.TZ


def ts(y, m, d, hh, mm):
    return int(datetime(y, m, d, hh, mm, tzinfo=TZ).timestamp())


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "z.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    c = db.connect(path)
    for i, n in ((1, "Justo"), (2, "Deli"), (3, "Sonia")):
        c.execute("INSERT INTO agents (id, name, informed_on, created_at) VALUES (?,?,'2026-10-01',0)", (i, n))
    for n, f in (("Centro", A), ("Ensanche", B), ("Estación", C)):
        zones.create_zone(c, n, FC(f), "2000-01-01")
    c.commit()
    yield c
    c.close()


def put_hour(c, agent, y, m, d, hh, pos):
    for i in range(60):
        c.execute("INSERT INTO positions VALUES (?,?,?,?,15,'ok',0)", (agent, ts(y, m, d, hh, i) + 20, *pos))


def test_three_zones_union(conn):
    for z in ("Centro", "Ensanche", "Estación"):
        zones.assign(conn, 1, z, "2026-10-01")
    put_hour(conn, 1, 2026, 10, 22, 10, IN_A)
    put_hour(conn, 1, 2026, 10, 22, 11, IN_B)
    put_hour(conn, 1, 2026, 10, 22, 12, IN_C)
    t = classify_day(conn, 1, date(2026, 10, 22), until_ts=ts(2026, 10, 22, 13, 0))
    assert t.inside_min == 180 and t.outside_min == 0


def test_only_assigned_zones_count(conn):
    zones.assign(conn, 2, "Centro", "2026-10-01")
    put_hour(conn, 2, 2026, 10, 22, 10, IN_A)
    put_hour(conn, 2, 2026, 10, 22, 11, IN_B)   # Ensanche no es suya
    t = classify_day(conn, 2, date(2026, 10, 22), until_ts=ts(2026, 10, 22, 12, 0))
    assert (t.inside_min, t.outside_min) == (60, 60)


def test_split_departing_agent_zones(conn):
    """Sonia se va: Centro pasa a Justo y Ensanche a Deli desde el 26/10."""
    zones.assign(conn, 3, "Centro", "2026-10-01")
    zones.assign(conn, 3, "Ensanche", "2026-10-01")
    zones.assign(conn, 1, "Estación", "2026-10-01")
    zones.transfer(conn, "Centro", 3, 1, "2026-10-26")
    zones.transfer(conn, "Ensanche", 3, 2, "2026-10-26")
    assert zones.agent_zone_names(conn, 3, date(2026, 10, 23)) == ["Centro", "Ensanche"]
    assert zones.agent_zone_names(conn, 3, date(2026, 10, 26)) == []
    assert zones.agent_zone_names(conn, 1, date(2026, 10, 23)) == ["Estación"]
    assert zones.agent_zone_names(conn, 1, date(2026, 10, 26)) == ["Centro", "Estación"]
    assert zones.agent_zone_names(conn, 2, date(2026, 10, 26)) == ["Ensanche"]
    # el viernes 23 Justo en Centro estaba FUERA (aún era de Sonia); el lunes 26, dentro
    put_hour(conn, 1, 2026, 10, 23, 10, IN_A)
    put_hour(conn, 1, 2026, 10, 26, 10, IN_A)
    t23 = classify_day(conn, 1, date(2026, 10, 23), until_ts=ts(2026, 10, 23, 11, 0))
    t26 = classify_day(conn, 1, date(2026, 10, 26), until_ts=ts(2026, 10, 26, 11, 0))
    assert (t23.inside_min, t23.outside_min) == (0, 60)
    assert (t26.inside_min, t26.outside_min) == (60, 0)


def test_shared_zone_and_no_double_assign(conn):
    zones.assign(conn, 1, "Centro", "2026-10-01")
    zones.assign(conn, 2, "Centro", "2026-10-01")          # dos comerciales en la misma zona: permitido
    assert [r["name"] for r in zones.holders(conn, 1, date(2026, 10, 2))] == ["Deli", "Justo"]
    with pytest.raises(zones.ZoneError):
        zones.assign(conn, 1, "Centro", "2026-10-15")      # ya la tiene


def test_unassign_and_reassign_later(conn):
    zones.assign(conn, 1, "Centro", "2026-10-01")
    zones.unassign(conn, 1, "Centro", "2026-10-10")
    zones.assign(conn, 1, "Centro", "2026-10-20")
    assert zones.agent_zone_names(conn, 1, date(2026, 10, 15)) == []
    assert zones.agent_zone_names(conn, 1, date(2026, 10, 20)) == ["Centro"]
    with pytest.raises(zones.ZoneError):
        zones.unassign(conn, 1, "Centro", "2026-10-15")    # ese día no la tenía…
    assert zones.agent_zone_names(conn, 1, date(2026, 10, 20)) == ["Centro"]   # …y no toca la futura


def test_baja_cancels_future_assignments(conn):
    zones.assign(conn, 1, "Centro", "2026-10-01")
    zones.assign(conn, 1, "Ensanche", "2026-12-01")
    assert zones.release_all(conn, 1, "2026-11-01") == ["Centro", "Ensanche"]
    assert zones.agent_zone_names(conn, 1, date(2026, 10, 31)) == ["Centro"]
    assert zones.agent_zone_names(conn, 1, date(2026, 12, 5)) == []


def test_unassign_future_assignment_cancels_it(conn):
    zones.assign(conn, 1, "Centro", "2026-11-02")
    zones.unassign(conn, 1, "Centro", "2026-11-02")
    assert conn.execute("SELECT COUNT(*) FROM zone_assignments").fetchone()[0] == 0


def test_temporary_assignment(conn):
    zones.assign(conn, 1, "Estación", "2026-11-02", "2026-11-16")   # cubrir vacaciones de otro
    assert zones.agent_zone_names(conn, 1, date(2026, 11, 15)) == ["Estación"]
    assert zones.agent_zone_names(conn, 1, date(2026, 11, 16)) == []


def test_reshape_zone_dated(conn):
    zones.assign(conn, 1, "Centro", "2026-10-01")
    zones.reshape_zone(conn, "Centro", FC(B), "2026-10-26")   # Centro se «mueve» a donde estaba B
    put_hour(conn, 1, 2026, 10, 23, 10, IN_A)
    put_hour(conn, 1, 2026, 10, 26, 10, IN_A)
    assert classify_day(conn, 1, date(2026, 10, 23), until_ts=ts(2026, 10, 23, 11, 0)).inside_min == 60
    assert classify_day(conn, 1, date(2026, 10, 26), until_ts=ts(2026, 10, 26, 11, 0)).outside_min == 60


def test_no_zone_means_not_measured(conn):
    put_hour(conn, 1, 2026, 10, 22, 10, IN_A)
    t = classify_day(conn, 1, date(2026, 10, 22), until_ts=ts(2026, 10, 22, 11, 0))
    assert t.zone_missing == 1 and t.inside_min == 0 and t.nodata_min == 60


def test_import_named_features(conn, tmp_path):
    f = tmp_path / "barrios.geojson"
    f.write_text(json.dumps({"type": "FeatureCollection", "features": [
        square(-3.30, 40.40, name="Chorrillo"), square(-3.29, 40.40, name="Reyes Católicos")]}))
    manage_main(["zonas-importar", str(f)])
    with db.session() as c:
        assert {r["name"] for r in c.execute("SELECT name FROM zone_defs")} >= {"Chorrillo", "Reyes Católicos"}
    bad = tmp_path / "bad.geojson"
    bad.write_text(json.dumps({"type": "FeatureCollection", "features": [square(-3.3, 40.4)]}))
    with pytest.raises(SystemExit, match="no tiene nombre"):
        manage_main(["zonas-importar", str(bad)])


def test_cli_transfer_and_baja(conn, capsys):
    conn.commit()
    manage_main(["asignar", "3", "Centro", "--desde", "2026-10-01"])
    manage_main(["asignar", "3", "Ensanche", "--desde", "2026-10-01"])
    manage_main(["traspasar", "Centro", "--de", "3", "--a", "1", "--desde", "2099-01-01"])
    manage_main(["baja", "3", "--desde", "2099-02-01"])
    out = capsys.readouterr().out
    assert "Zonas liberadas desde 2099-02-01: Ensanche" in out
    manage_main(["zonas", "--dia", "2099-03-01", "--historial"])
    out = capsys.readouterr().out
    assert "Ensanche" in out and "sin asignar" in out
    with pytest.raises(SystemExit, match="No existe la zona"):
        manage_main(["asignar", "1", "Inventada", "--desde", "2099-01-01"])


def test_migrates_legacy_bundles(tmp_path):
    """Base de datos del formato anterior (tabla `zones` con paquetes por comercial)."""
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.executescript("""
      CREATE TABLE agents (id INTEGER PRIMARY KEY, name TEXT NOT NULL, team TEXT, token_hash TEXT UNIQUE,
        telegram_id INTEGER UNIQUE, link_code TEXT, min_daily_minutes INTEGER, min_weekly_minutes INTEGER,
        informed_on TEXT, active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
      CREATE TABLE zones (id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL, name TEXT, geojson TEXT NOT NULL,
        valid_from TEXT NOT NULL, created_at INTEGER NOT NULL);
      INSERT INTO agents (id, name, informed_on, created_at) VALUES (1, 'Prueba Oscar', '2026-10-22', 0);
    """)
    c.execute("INSERT INTO zones VALUES (1, 1, 'Prueba Tinte-Teniente Ruiz', ?, '2026-10-22', 0)", (json.dumps(FC(A)),))
    c.execute("INSERT INTO zones VALUES (2, 1, NULL, ?, '2026-11-01', 0)", (json.dumps(FC(B)),))
    c.commit(); c.close()

    c = db.connect(path)
    c.close()
    c = db.connect(path)          # segunda conexión: no migra dos veces
    assert c.execute("SELECT COUNT(*) FROM zone_defs").fetchone()[0] == 2
    assert zones.agent_zone_names(c, 1, date(2026, 10, 25)) == ["Prueba Tinte-Teniente Ruiz"]
    assert zones.agent_zone_names(c, 1, date(2026, 11, 1)) == ["Zona comercial 1"]
    assert c.execute("SELECT name FROM sqlite_master WHERE name='zones'").fetchone() is None
    c.close()
