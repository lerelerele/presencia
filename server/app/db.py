import os
import sqlite3
import time
from contextlib import contextmanager

from . import config, zones

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    team TEXT,
    token_hash TEXT UNIQUE,
    telegram_id INTEGER UNIQUE,
    link_code TEXT,
    min_daily_minutes INTEGER,
    min_weekly_minutes INTEGER,
    informed_on TEXT,          -- fecha del acuse firmado; sin ella no se registra nada
    active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS holidays (day TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS absences (
    id INTEGER PRIMARY KEY,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    start_day TEXT NOT NULL,
    end_day TEXT NOT NULL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS justifications (
    id INTEGER PRIMARY KEY,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pendiente',   -- pendiente | aceptada | rechazada
    created_at INTEGER NOT NULL,
    decided_by TEXT,
    decided_at INTEGER
);
-- Posiciones brutas: se purgan a los RETENTION_DAYS días.
CREATE TABLE IF NOT EXISTS positions (
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    ts INTEGER NOT NULL,
    lat REAL,
    lon REAL,
    accuracy REAL,
    status TEXT NOT NULL,       -- ok | gps_off | no_permission | mock
    received_at INTEGER NOT NULL,
    PRIMARY KEY (agent_id, ts)
);
-- Muestras recibidas fuera de horario: solo el recuento, nunca la posición.
CREATE TABLE IF NOT EXISTS discarded_samples (
    agent_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (agent_id, day)
);
CREATE TABLE IF NOT EXISTS daily_totals (
    agent_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    scheduled_min INTEGER NOT NULL,
    inside_min INTEGER NOT NULL,
    outside_min INTEGER NOT NULL,
    justified_min INTEGER NOT NULL,
    nodata_min INTEGER NOT NULL,
    gps_off_min INTEGER NOT NULL,
    mock_min INTEGER NOT NULL,
    zone_missing INTEGER NOT NULL DEFAULT 0,
    computed_at INTEGER NOT NULL,
    PRIMARY KEY (agent_id, day)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    zones.ensure_schema(conn)
    conn.commit()
    return conn


@contextmanager
def session(path: str | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def audit(conn: sqlite3.Connection, actor: str, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, detail) VALUES (?,?,?,?)",
        (int(time.time()), actor, action, detail),
    )
