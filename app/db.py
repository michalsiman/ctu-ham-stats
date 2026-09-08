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

CREATE TABLE IF NOT EXISTS callsigns (
    callsign    TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_callsigns_last_seen ON callsigns (last_seen);

CREATE TABLE IF NOT EXISTS daily_stats (
    snapshot_date    TEXT PRIMARY KEY,  -- ISO datum snapshotu
    total_rows       INTEGER NOT NULL,  -- počet řádků v CSV (oprávnění)
    unique_callsigns INTEGER NOT NULL,  -- počet unikátních značek
    added            INTEGER,           -- nové značky proti předchozímu snapshotu
    removed          INTEGER,           -- zmizelé značky proti předchozímu snapshotu
    fetched_at       TEXT NOT NULL      -- UTC timestamp stažení
);

CREATE TABLE IF NOT EXISTS app_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS page_visits (
    visited_on     TEXT NOT NULL,      -- ISO datum (YYYY-MM-DD)
    visitor_hash   TEXT NOT NULL,      -- anonymizovaný otisk návštěvníka
    country_code   TEXT NOT NULL,      -- ISO-3166-1 alpha-2, nebo ZZ
    first_seen_at  TEXT NOT NULL,      -- UTC timestamp první návštěvy v daný den
    last_seen_at   TEXT NOT NULL,      -- UTC timestamp poslední návštěvy v daný den
    hits           INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (visited_on, visitor_hash)
);

CREATE INDEX IF NOT EXISTS idx_page_visits_day_country
ON page_visits (visited_on, country_code);

-- Odvozený okres ke značce (POC + mapa). PRIVACY-BY-DESIGN: ukládá se výhradně
-- dvojice značka→okres a nenosobní metadata. Žádné jméno, adresa, PSČ,
-- souřadnice ani lokátor se sem NIKDY neukládají – z odpovědí callbooků se
-- v paměti spočítá jen okres a zbytek se zahodí. Veřejně se publikují pouze
-- agregované počty na okres.
CREATE TABLE IF NOT EXISTS callsign_okres (
    callsign   TEXT PRIMARY KEY,
    okres      TEXT,               -- odvozený okres (NULL = nedohledáno)
    source     TEXT,               -- zdroj, který okres dodal (qrz/hamqth/qrzcq)
    method     TEXT,               -- metoda odvození (latlon/grid/zip/obec/znak)
    found      INTEGER NOT NULL DEFAULT 0,  -- existoval veřejný profil (bool)
    exhausted  INTEGER NOT NULL DEFAULT 0,  -- vyzkoušeny všechny dostupné zdroje
    fetched_at TEXT NOT NULL        -- UTC timestamp zpracování
);

-- Odvozený okres+kraj ke značce obohacený o územní kódy (LAU/NUTS/ISO) pro mapu
-- a agregované počty. Plní se z callsign_okres přes app.region.refresh_region()
-- (denní job) nebo importem CSV (scripts/import_okres_csv.py). Nezávislá na
-- callsign_okres – drží jen značky s vyřešeným okresem.
CREATE TABLE IF NOT EXISTS callsign_region (
    callsign  TEXT PRIMARY KEY,
    okres     TEXT NOT NULL,   -- název okresu
    okres_lau TEXT NOT NULL,   -- LAU kód okresu (CZ0xxx)
    kraj      TEXT NOT NULL,   -- název kraje
    kraj_nuts TEXT NOT NULL,   -- NUTS3 kód kraje (CZ0xx)
    kraj_iso  TEXT NOT NULL    -- ISO 3166-2 kód kraje (CZ-xx)
);
CREATE INDEX IF NOT EXISTS idx_callsign_region_kraj ON callsign_region(kraj_iso);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        """
        INSERT OR IGNORE INTO callsigns (callsign, first_seen, last_seen)
        SELECT callsign, MIN(first_seen), MAX(last_seen)
        FROM licenses
        GROUP BY callsign
        """
    )
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


def record_visit(
    conn: sqlite3.Connection,
    visited_on: str,
    visitor_hash: str,
    country_code: str,
    seen_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO page_visits (
            visited_on,
            visitor_hash,
            country_code,
            first_seen_at,
            last_seen_at,
            hits
        )
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(visited_on, visitor_hash) DO UPDATE SET
            country_code = excluded.country_code,
            last_seen_at = excluded.last_seen_at,
            hits = page_visits.hits + 1
        """,
        (visited_on, visitor_hash, country_code, seen_at, seen_at),
    )
