"""SQLite vrstva: schéma a připojení."""
import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS licenses (
    callsign    TEXT NOT NULL,
    reference   INTEGER NOT NULL,
    valid_until TEXT NOT NULL,          -- ISO datum (YYYY-MM-DD)
    first_seen  TEXT NOT NULL,          -- datum snapshotu, kdy se záznam objevil
    last_seen   TEXT NOT NULL,          -- datum posledního snapshotu, kde byl
    PRIMARY KEY (callsign, reference, valid_until)
);

CREATE INDEX IF NOT EXISTS idx_licenses_last_seen ON licenses (last_seen);
CREATE INDEX IF NOT EXISTS idx_licenses_valid_until ON licenses (valid_until);

CREATE TABLE IF NOT EXISTS daily_stats (
    snapshot_date    TEXT PRIMARY KEY,  -- ISO datum snapshotu
    total_rows       INTEGER NOT NULL,  -- počet řádků v CSV (oprávnění)
    unique_callsigns INTEGER NOT NULL,  -- počet unikátních značek
    added            INTEGER,           -- nové záznamy proti předchozímu snapshotu
    removed          INTEGER,           -- zmizelé záznamy proti předchozímu snapshotu
    fetched_at       TEXT NOT NULL      -- UTC timestamp stažení
);

CREATE TABLE IF NOT EXISTS app_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def set_state(conn: sqlite3.Connection, key: str, value: str, updated_at: str) -> None:
    conn.execute(
        """
        INSERT INTO app_state (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value, updated_at),
    )
