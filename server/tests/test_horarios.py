"""Horario propio por comercial."""
import json
import time
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import api, config, db, schedule, zones
from app.manage import main as manage_main
from app.totals import classify_day

TZ = config.TZ
SQ = {"type": "Polygon", "coordinates": [[[-3.37, 40.48], [-3.36, 40.48], [-3.36, 40.49], [-3.37, 40.49], [-3.37, 40.48]]]}
IN = (40.485, -3.365)


def ts(y, m, d, hh, mm):
    return int(datetime(y, m, d, hh, mm, tzinfo=TZ).timestamp())


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "h.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    c = db.connect(path)
    for i in (1, 2):
        c.execute("INSERT INTO agents (id, name, token_hash, informed_on, created_at) VALUES (?,?,?,'2026-11-01',0)",
                  (i, f"C{i}", api.token_hash(f"t{i}")))
    zones.create_zone(c, "Z", SQ, "2000-01-01")
    zones.assign(c, 1, "Z", "2026-11-01"); zones.assign(c, 2, "Z", "2026-11-01")
    c.commit()
    yield c
    c.close()


def hours(ws):
    return [(datetime.fromtimestamp(a.timestamp(), TZ).strftime("%H:%M"),
             datetime.fromtimestamp(b.timestamp(), TZ).strftime("%H:%M")) for a, b in ws]


def test_own_schedule_from_date(conn):
    conn.execute("INSERT INTO agent_schedules (agent_id, valid_from, spec, created_at) VALUES (1,'2026-11-23','L-V 11:00-19:00',0)")
    # antes de la fecha: general
    assert hours(schedule.agent_windows(conn, 1, date(2026, 11, 20))) == [("10:00", "14:00"), ("17:00", "20:30")]
    # desde la fecha: el suyo; sábado sin jornada
    assert hours(schedule.agent_windows(conn, 1, date(2026, 11, 23))) == [("11:00", "19:00")]
    assert schedule.agent_windows(conn, 1, date(2026, 11, 28)) == []
    # el otro comercial no cambia
    assert hours(schedule.agent_windows(conn, 2, date(2026, 11, 23))) == [("10:00", "14:00"), ("17:00", "20:30")]
    # vuelta al general
    conn.execute("INSERT INTO agent_schedules (agent_id, valid_from, spec, created_at) VALUES (1,'2026-12-01',NULL,0)")
    assert hours(schedule.agent_windows(conn, 1, date(2026, 12, 1))) == [("10:00", "14:00"), ("17:00", "20:30")]


def test_15h_counts_only_with_own_schedule(conn):
    conn.execute("INSERT INTO agent_schedules (agent_id, valid_from, spec, created_at) VALUES (1,'2026-11-23','L-V 11:00-19:00',0)")
    for a in (1, 2):
        for i in range(60):
            conn.execute("INSERT INTO positions VALUES (?,?,?,?,15,'ok',0)", (a, ts(2026, 11, 23, 15, i) + 20, *IN))
    t1 = classify_day(conn, 1, date(2026, 11, 23))
    t2 = classify_day(conn, 2, date(2026, 11, 23))
    assert t1.scheduled_min == 480 and t1.inside_min == 62   # 60 + arrastre de la última muestra
    assert t2.scheduled_min == 450 and t2.inside_min == 0   # 15:00 es su pausa: ni se cuenta


def test_api_accepts_and_discards_by_own_schedule(conn, monkeypatch):
    conn.execute("INSERT INTO agent_schedules (agent_id, valid_from, spec, created_at) VALUES (1,'2026-11-23','L-V 11:00-19:00',0)")
    conn.commit()
    monkeypatch.setattr(time, "time", lambda: ts(2026, 11, 23, 21, 0))
    c = TestClient(api.app)
    smp = [{"ts": ts(2026, 11, 23, h, mi), "status": "ok", "lat": IN[0], "lon": IN[1], "accuracy": 10} for h, mi in ((10, 0), (15, 0), (19, 5))]
    r1 = c.post("/api/v1/samples", json={"samples": smp}, headers={"Authorization": "Bearer t1"}).json()
    r2 = c.post("/api/v1/samples", json={"samples": smp}, headers={"Authorization": "Bearer t2"}).json()
    assert r1 == {"accepted": 1, "discarded": 2}   # solo 15:00 (10:00 y 19:05 fuera de su jornada)
    assert r2 == {"accepted": 2, "discarded": 1}   # 10:00 y 19:05; las 15:00 son su pausa
    monkeypatch.setattr(time, "time", lambda: ts(2026, 11, 20, 9, 0))
    s = c.get("/api/v1/schedule", headers={"Authorization": "Bearer t1"}).json()
    assert s["horario"].startswith("L-V 10:00-14:00 y 17:00-20:30; S 10:30-14:00 (desde el 23/11: L-V 11:00-19:00)")


def test_cli_horario(conn, capsys):
    manage_main(["horario", "1", "L-V 11:00-19:00", "--desde", "2099-01-05"])
    manage_main(["listar"])
    out = capsys.readouterr().out
    assert "desde 2099-01-05 su jornada es L-V 11:00-19:00" in out
    with pytest.raises(SystemExit, match="No entiendo"):
        manage_main(["horario", "1", "lunes a viernes", "--desde", "2099-01-05"])
    with pytest.raises(SystemExit, match="uno de los dos"):
        manage_main(["horario", "1", "--desde", "2099-01-05"])
    manage_main(["horario", "1", "--general", "--desde", "2099-02-01"])
    with db.session() as c:
        assert schedule.agent_weekly(c, 1, date(2099, 2, 2))[1] is True
