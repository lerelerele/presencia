import json
import time
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import api, config, db, geo, reports, schedule
from app.manage import main as manage_main, purge, pilot_metrics
from app.totals import classify_day, recompute

# Cuadrado de ~1 km en el centro de una ciudad cualquiera
ZONE = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {
    "type": "Polygon", "coordinates": [[[-2.470, 36.835], [-2.459, 36.835], [-2.459, 36.844],
                                        [-2.470, 36.844], [-2.470, 36.835]]]}}]}
IN = (36.8395, -2.4645)         # centro
OUT = (36.8600, -2.4645)        # ~1,8 km al norte
TZ = config.TZ


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    zone_file = tmp_path / "z.geojson"
    zone_file.write_text(json.dumps(ZONE))
    conn = db.connect(path)
    token = "tok-justo"
    conn.execute("INSERT INTO agents (id, name, team, token_hash, min_daily_minutes, min_weekly_minutes, informed_on, created_at) "
                 "VALUES (1,'Justo','Ángel',?,180,900,'2026-10-01',0)", (api.token_hash(token),))
    conn.execute("INSERT INTO zones (agent_id, geojson, valid_from, created_at) VALUES (1,?, '2026-10-01', 0)",
                 (json.dumps(ZONE),))
    conn.commit()
    yield conn, token, zone_file
    conn.close()


def ts(y, m, d, hh, mm, ss=0):
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=TZ).timestamp())


# ── Horario ──

def test_weekday_saturday_sunday():
    assert [(a.hour, a.minute, b.hour, b.minute) for a, b in schedule.day_windows(date(2026, 10, 22))] == \
        [(10, 0, 14, 0), (17, 0, 20, 30)]
    assert [(a.hour, a.minute, b.hour) for a, b in schedule.day_windows(date(2026, 10, 24))] == [(10, 30, 14)]
    assert schedule.day_windows(date(2026, 10, 25)) == []


def test_dst_change_monday_after():
    # El cambio de hora es el domingo 25/10/2026; el lunes 26 la ventana sigue siendo 10:00 local (UTC+1).
    a, b = schedule.day_windows(date(2026, 10, 26))[0]
    assert a.utcoffset() == timedelta(hours=1)
    assert datetime.fromtimestamp(a.timestamp(), TZ).hour == 10
    a2, _ = schedule.day_windows(date(2026, 10, 23))[0]
    assert a2.utcoffset() == timedelta(hours=2)


def test_no_windows_without_acuse_holiday_absence(env):
    conn, *_ = env
    d = date(2026, 10, 22)
    assert schedule.agent_windows(conn, 1, d)
    conn.execute("INSERT INTO holidays VALUES ('2026-10-22','x')")
    assert schedule.agent_windows(conn, 1, d) == []
    conn.execute("DELETE FROM holidays")
    conn.execute("INSERT INTO absences (agent_id,start_day,end_day) VALUES (1,'2026-10-21','2026-10-23')")
    assert schedule.agent_windows(conn, 1, d) == []
    conn.execute("DELETE FROM absences")
    conn.execute("UPDATE agents SET informed_on=NULL")
    assert schedule.agent_windows(conn, 1, d) == []
    conn.execute("UPDATE agents SET informed_on='2026-10-23'")
    assert schedule.agent_windows(conn, 1, d) == []   # antes de la fecha del acuse


# ── Geocerca ──

def test_geofence():
    polys = geo.parse_zones(ZONE)
    assert geo.in_polygon(IN[1], IN[0], polys[0])
    assert not geo.in_polygon(OUT[1], OUT[0], polys[0])
    d = geo.distance_to_zones_m(OUT[1], OUT[0], polys)
    assert 1700 < d < 1900
    # 50 m al norte del borde: dentro con tolerancia 100, fuera con 20
    lat = 36.844 + 50 / 110540
    assert geo.inside_with_tolerance(-2.4645, lat, polys, 100)
    assert not geo.inside_with_tolerance(-2.4645, lat, polys, 20)


def test_geofence_hole():
    z = {"type": "Polygon", "coordinates": [
        [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]],
        [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6], [0.4, 0.4]]]}
    p = geo.parse_zones(z)[0]
    assert geo.in_polygon(0.2, 0.2, p) and not geo.in_polygon(0.5, 0.5, p)


# ── API: lo de fuera de jornada no se guarda ──

def test_api_discards_outside_schedule(env, monkeypatch):
    conn, token, _ = env
    now = ts(2026, 10, 22, 21, 0)
    monkeypatch.setattr(time, "time", lambda: now)
    c = TestClient(api.app)
    h = {"Authorization": f"Bearer {token}"}
    samples = [
        {"ts": ts(2026, 10, 22, 9, 59), "status": "ok", "lat": IN[0], "lon": IN[1], "accuracy": 10},   # antes
        {"ts": ts(2026, 10, 22, 10, 5), "status": "ok", "lat": IN[0], "lon": IN[1], "accuracy": 10},   # dentro
        {"ts": ts(2026, 10, 22, 15, 0), "status": "ok", "lat": IN[0], "lon": IN[1], "accuracy": 10},   # pausa
        {"ts": ts(2026, 10, 22, 20, 45), "status": "ok", "lat": IN[0], "lon": IN[1], "accuracy": 10},  # después
        {"ts": ts(2026, 10, 25, 11, 0), "status": "ok", "lat": IN[0], "lon": IN[1], "accuracy": 10},   # domingo/futuro
    ]
    r = c.post("/api/v1/samples", json={"samples": samples}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"accepted": 1, "discarded": 4}
    stored = conn.execute("SELECT ts FROM positions").fetchall()
    assert [s["ts"] for s in stored] == [samples[1]["ts"]]
    assert conn.execute("SELECT SUM(count) FROM discarded_samples").fetchone()[0] == 3
    # reenvío idempotente
    assert c.post("/api/v1/samples", json={"samples": samples[1:2]}, headers=h).json()["accepted"] == 0
    assert c.post("/api/v1/samples", json={"samples": []}, headers={"Authorization": "Bearer x"}).status_code == 401


def test_api_schedule_and_me(env, monkeypatch):
    conn, token, _ = env
    now = ts(2026, 10, 22, 12, 0)
    monkeypatch.setattr(time, "time", lambda: now)
    c = TestClient(api.app)
    h = {"Authorization": f"Bearer {token}"}
    s = c.get("/api/v1/schedule", headers=h).json()
    assert s["windows"][0] == [ts(2026, 10, 22, 10, 0), ts(2026, 10, 22, 14, 0)]
    assert all(datetime.fromtimestamp(a, TZ).weekday() != 6 for a, _ in s["windows"])
    me = c.get("/api/v1/me", headers=h).json()
    assert me["periods"]["hoy"]["jornada_min"] == 120   # 10:00–12:00 transcurrido
    assert "lat" not in json.dumps(me)


# ── Clasificación por minutos ──

def put(conn, t, status="ok", pos=IN, acc=15):
    lat, lon = pos if status == "ok" else (None, None)
    conn.execute("INSERT INTO positions VALUES (1,?,?,?,?,?,0)", (t, lat, lon, acc, status))


def test_classification_mix(env):
    conn, *_ = env
    d = date(2026, 10, 22)
    # 10:00–11:00 dentro (una muestra/min)
    for i in range(60):
        put(conn, ts(2026, 10, 22, 10, i, 20))
    # 11:00–11:30 fuera
    for i in range(30):
        put(conn, ts(2026, 10, 22, 11, i, 20), pos=OUT)
    # 11:30–11:40 GPS apagado
    for i in range(30, 40):
        put(conn, ts(2026, 10, 22, 11, i, 20), status="gps_off")
    # 11:40–11:45 simulada
    for i in range(40, 45):
        put(conn, ts(2026, 10, 22, 11, i, 20), status="mock")
    # 11:45–14:00 nada → sin datos (salvo arrastre de 2 min de la última muestra)
    # tarde: 17:00–18:00 fuera, pero justificado 17:00–17:30
    for i in range(60):
        put(conn, ts(2026, 10, 22, 17, i, 10), pos=OUT)
    conn.execute("INSERT INTO justifications (agent_id,start_ts,end_ts,reason,status,created_at) "
                 "VALUES (1,?,?,'notaría','aceptada',0)", (ts(2026, 10, 22, 17, 0), ts(2026, 10, 22, 17, 30)))
    t = classify_day(conn, 1, d)
    assert t.scheduled_min == 450
    assert t.inside_min == 60
    assert t.justified_min == 30
    assert t.outside_min == 30 + 30 + 2     # mañana 30 + tarde 30 sin justificar + arrastre 2 min
    assert t.mock_min == 5 + 2               # 5 min + arrastre de la última muestra simulada
    assert t.gps_off_min == 10
    total = t.inside_min + t.outside_min + t.justified_min + t.nodata_min + t.gps_off_min + t.mock_min
    assert total == t.scheduled_min


def test_bad_accuracy_and_boundary(env):
    conn, *_ = env
    put(conn, ts(2026, 10, 22, 10, 0, 10), pos=OUT, acc=900)                         # precisión inútil
    put(conn, ts(2026, 10, 22, 10, 1, 10), pos=(36.844 + 60 / 110540, -2.4645), acc=80)  # 60 m fuera, acc 80
    t = classify_day(conn, 1, date(2026, 10, 22), until_ts=ts(2026, 10, 22, 10, 2))
    assert (t.nodata_min, t.inside_min, t.outside_min) == (1, 1, 0)


def test_report_has_no_coordinates(env):
    conn, *_ = env
    for i in range(60):
        put(conn, ts(2026, 10, 22, 10, i, 20))
    recompute(conn, date(2026, 10, 22), date(2026, 10, 22))
    txt = reports.build(conn, "dia", date(2026, 10, 22))
    assert "Justo" in txt and "PILOTO" in txt
    assert "36.8" not in txt and "-2.4" not in txt
    assert "faltan" in txt   # mínimo 3 h, solo 1 h dentro


def test_purge_retention(env, monkeypatch):
    conn, *_ = env
    put(conn, ts(2026, 10, 1, 10, 0))
    put(conn, ts(2026, 10, 22, 10, 0))
    monkeypatch.setattr(time, "time", lambda: ts(2026, 11, 6, 3, 0))   # 36 días después del 1/10
    assert purge(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1


def test_cli_alta_and_metrics(env, capsys):
    conn, _, zone_file = env
    conn.commit()
    manage_main(["alta", "--nombre", "Sonia", "--equipo", "Ángel", "--min-dia", "2h30m"])
    out = capsys.readouterr().out
    assert "Token del móvil" in out
    manage_main(["zona", "2", str(zone_file), "--desde", "2099-01-01"])
    manage_main(["listar"])
    assert "Sonia" in capsys.readouterr().out
    assert "Fiabilidad" in pilot_metrics(conn, date(2026, 10, 19), date(2026, 10, 24))
