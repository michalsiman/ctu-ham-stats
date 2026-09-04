#!/usr/bin/env python3
"""Import CSV s okresy/kraji do hlavní DB projektu.

Nahraje CSV vyrobené `scripts/export_okres_csv.py`
(`callsign,okres,okres_lau,kraj,kraj_nuts,kraj_iso`) do samostatné tabulky
`callsign_region`. Tabulka je nezávislá na zbytku schématu – stačí ji přiložit
a mapa z ní čerpá agregované počty na kraj (a později okres).

Vlastnosti:
- Tabulku vytvoří, když neexistuje (`CREATE TABLE IF NOT EXISTS`).
- `INSERT OR REPLACE` podle značky (idempotentní – opakovaný import přepíše).
- Nic nemaže: značky, které v CSV nejsou, v tabulce zůstanou.
- Před importem ověří hlavičku CSV a data nechá projít validací (známé kódy).

Příklad dotazu pro krajovou mapu (jen aktuálně platné značky):
    SELECT r.kraj_iso, r.kraj, COUNT(*) AS pocet
    FROM callsign_region r
    JOIN callsigns c ON c.callsign = r.callsign
    WHERE c.last_seen = (SELECT MAX(last_seen) FROM callsigns)
    GROUP BY r.kraj_iso ORDER BY pocet DESC;

Použití:
    python scripts/import_okres_csv.py callsign_okres.csv --db data/hamstats.db
    python scripts/import_okres_csv.py callsign_okres.csv --db data/hamstats.db --dry-run
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("import_okres")

EXPECTED_HEADER = ["callsign", "okres", "okres_lau", "kraj", "kraj_nuts", "kraj_iso"]

SCHEMA = """
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


def read_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != EXPECTED_HEADER:
            log.error("Neočekávaná hlavička CSV.\n  čekáno: %s\n  nalezeno: %s",
                      EXPECTED_HEADER, header)
            sys.exit(1)
        rows = []
        for i, rec in enumerate(reader, start=2):
            if len(rec) != len(EXPECTED_HEADER):
                log.error("Řádek %d má %d sloupců (čekáno %d): %s",
                          i, len(rec), len(EXPECTED_HEADER), rec)
                sys.exit(1)
            row = dict(zip(EXPECTED_HEADER, rec))
            if not row["callsign"] or not row["okres"]:
                log.error("Řádek %d: prázdná značka nebo okres: %s", i, rec)
                sys.exit(1)
            rows.append(row)
    return rows


def import_csv(db_path: Path, csv_path: Path, dry_run: bool) -> None:
    rows = read_rows(csv_path)
    log.info("Načteno %d řádků z %s", len(rows), csv_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    before = conn.execute("SELECT COUNT(*) FROM callsign_region").fetchone()[0]
    existing = {r[0] for r in conn.execute("SELECT callsign FROM callsign_region")}
    new = sum(1 for r in rows if r["callsign"] not in existing)
    updated = len(rows) - new

    if dry_run:
        log.info("[dry-run] přibylo by %d, aktualizovalo %d (stávající: %d). "
                 "Nic nezapsáno.", new, updated, before)
        return

    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO callsign_region "
            "(callsign, okres, okres_lau, kraj, kraj_nuts, kraj_iso) "
            "VALUES (:callsign, :okres, :okres_lau, :kraj, :kraj_nuts, :kraj_iso)",
            rows,
        )
    after = conn.execute("SELECT COUNT(*) FROM callsign_region").fetchone()[0]
    log.info("Hotovo: nových %d, aktualizovaných %d, celkem v tabulce %d.",
             new, updated, after)

    log.info("Rozpad podle kraje:")
    for r in conn.execute(
        "SELECT kraj, kraj_iso, COUNT(*) c FROM callsign_region "
        "GROUP BY kraj_iso ORDER BY c DESC"
    ):
        log.info("  %-24s %s  %d", r["kraj"], r["kraj_iso"], r["c"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path, help="vstupní CSV (callsign_okres.csv)")
    ap.add_argument("--db", type=Path, default=Path("data/hamstats.db"),
                    help="cílová SQLite DB (default: %(default)s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="jen spočítat dopad, nic nezapsat")
    args = ap.parse_args()

    if not args.csv.exists():
        log.error("CSV neexistuje: %s", args.csv)
        sys.exit(1)
    if not args.db.exists():
        log.error("Cílová DB neexistuje: %s", args.db)
        sys.exit(1)

    import_csv(args.db, args.csv, args.dry_run)


if __name__ == "__main__":
    main()
