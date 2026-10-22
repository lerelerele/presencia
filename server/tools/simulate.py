"""Simula una jornada de piloto con dos comerciales y saca el informe y las métricas.
Uso: DB_PATH=/tmp/sim.db python -m tools.simulate 2026-10-22
Envía las muestras por la API real, así que también comprueba el descarte fuera de horario."""
import json
import random
import sys
import time
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from app import api, config, db, reports
from app.manage import pilot_metrics
from app.totals import recompute

ZONE = {"type": "Polygon", "coordinates": [[[-2.470, 36.835], [-2.459, 36.835], [-2.459, 36.844],
                                            [-2.470, 36.844], [-2.470, 36.835]]]}


def main():
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 10, 22)
    rnd = random.Random(7)
    conn = db.connect()
    agents = {}
    for name, team in (("Comercial A", "Ángel"), ("Comercial B", "Ángeles")):
        tok = f"sim-{name}"
        cur = conn.execute("INSERT INTO agents (name, team, token_hash, min_daily_minutes, min_weekly_minutes, "
                           "informed_on, created_at) VALUES (?,?,?,?,?,?,0)",
                           (name, team, api.token_hash(tok), 240, 1200, (d - timedelta(days=7)).isoformat()))
        conn.execute("INSERT INTO zones (agent_id, geojson, valid_from, created_at) VALUES (?,?,?,0)",
                     (cur.lastrowid, json.dumps(ZONE), (d - timedelta(days=7)).isoformat()))
        agents[name] = tok
    conn.commit()

    def at(h, m):
        return int(datetime(d.year, d.month, d.day, h, m, tzinfo=config.TZ).timestamp())

    plans = {
        # A: casi todo dentro, 40 min fuera por la tarde, un hueco sin cobertura
        "Comercial A": [(at(9, 50), at(14, 10), "in"), (at(17, 0), at(18, 0), "in"),
                        (at(18, 0), at(18, 40), "out"), (at(18, 55), at(20, 40), "in")],
        # B: mañana a medias, apaga el GPS 20 min
        "Comercial B": [(at(10, 0), at(11, 30), "in"), (at(11, 30), at(12, 45), "out"),
                        (at(12, 45), at(13, 5), "gps_off"), (at(13, 5), at(14, 0), "in"),
                        (at(17, 0), at(20, 30), "in")],
    }
    real_time = time.time
    time.time = lambda: at(23, 0)   # como si fueran las 23:00 de ese día
    client = TestClient(api.app)
    for name, plan in plans.items():
        samples = []
        for a, b, kind in plan:
            t = a
            while t < b:
                t += 60 + rnd.randint(-8, 8)
                if kind == "gps_off":
                    samples.append({"ts": t, "status": "gps_off"})
                    continue
                lat = 36.8395 + rnd.uniform(-0.003, 0.003) + (0.02 if kind == "out" else 0)
                samples.append({"ts": t, "status": "ok", "lat": lat, "lon": -2.4645 + rnd.uniform(-0.003, 0.003),
                                "accuracy": rnd.choice([8, 12, 20, 35])})
        r = client.post("/api/v1/samples", json={"samples": samples},
                        headers={"Authorization": f"Bearer {agents[name]}"})
        print(f"{name}: {r.json()}")
    conn.close()
    conn = db.connect()
    try:
        recompute(conn, d, d)
        conn.commit()
        print()
        print(reports.build(conn, "dia", d).replace("<b>", "").replace("</b>", ""))
        print()
        print(pilot_metrics(conn, d, d))
    finally:
        time.time = real_time


if __name__ == "__main__":
    main()
