"""Promítnutí odvozeného okresu do tabulky `callsign_region` (mapa + agregace).

Z `callsign_okres` (kde `okres IS NOT NULL`) obohatí název okresu o územní kódy
(LAU/kraj/NUTS/ISO) přes `app.kraje` a upsertuje do `callsign_region`. Stejné
obohacení (`region_row`) používá i CSV export (`scripts/export_okres_csv.py`).

PRIVACY-BY-DESIGN: pracuje výhradně se značkou→okres→kraj + kanonické kódy.
Žádné jméno, adresa, PSČ, souřadnice ani lokátor.
"""
from __future__ import annotations

import logging
import sqlite3

from . import kraje

log = logging.getLogger(__name__)

COLUMNS = ["callsign", "okres", "okres_lau", "kraj", "kraj_nuts", "kraj_iso"]


def region_row(okres: str) -> dict | None:
    """Obohatí název okresu na řádek s územními kódy (bez `callsign`).

    Vrací None, když okres nejde napojit na kraj (chybí alias v `app/kraje.py`).
    """
    info = kraje.lookup(okres)
    if info is None:
        return None
    return {
        "okres": info.okres,
        "okres_lau": info.lau,
        "kraj": info.kraj.nazev,
        "kraj_nuts": info.kraj.nuts,
        "kraj_iso": info.kraj.iso,
    }


def refresh_region(conn: sqlite3.Connection) -> dict:
    """Přegeneruje `callsign_region` z `callsign_okres` (okres IS NOT NULL).

    `INSERT OR REPLACE` podle značky (idempotentní). Nenapojené okresy nezahazuje
    tiše – zaloguje je. Vrací statistiku {written, skipped, unmapped}.
    """
    rows = conn.execute(
        "SELECT callsign, okres FROM callsign_okres "
        "WHERE okres IS NOT NULL ORDER BY callsign"
    ).fetchall()

    payload: list[dict] = []
    unmapped: dict[str, int] = {}
    for r in rows:
        row = region_row(r["okres"])
        if row is None:
            unmapped[r["okres"]] = unmapped.get(r["okres"], 0) + 1
            continue
        row["callsign"] = r["callsign"]
        payload.append(row)

    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO callsign_region "
            "(callsign, okres, okres_lau, kraj, kraj_nuts, kraj_iso) "
            "VALUES (:callsign, :okres, :okres_lau, :kraj, :kraj_nuts, :kraj_iso)",
            payload,
        )

    skipped = sum(unmapped.values())
    if unmapped:
        log.warning("callsign_region: nenapojené okresy (přeskočeny, doplň alias "
                    "v app/kraje.py):")
        for name, cnt in sorted(unmapped.items(), key=lambda x: -x[1]):
            log.warning("  %r: %d značek", name, cnt)
    log.info("callsign_region: zapsáno %d značek (přeskočeno %d).",
             len(payload), skipped)
    return {"written": len(payload), "skipped": skipped, "unmapped": dict(unmapped)}
