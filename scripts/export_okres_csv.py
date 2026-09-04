#!/usr/bin/env python3
"""Export vyřešených okresů do CSV pro autora projektu.

Z tabulky `callsign_okres` (viz `scripts/qrz_poc.py`) vezme všechny značky
s odvozeným okresem, doplní kraj + územní kódy (`app/kraje.py`) a zapíše CSV:

    callsign,okres,okres_lau,kraj,kraj_nuts,kraj_iso

PRIVACY-BY-DESIGN: exportuje se VÝHRADNĚ značka→okres→kraj + kanonické kódy.
Žádné jméno, adresa, PSČ, souřadnice ani lokátor – ta se v DB vůbec neukládají.

Rozsah: všechny značky s `okres IS NOT NULL` (bez ohledu na aktivitu). Filtraci
na aktuálně platné značky si dělá autor ve své DB přes `last_seen` – proto sem
posíláme kompletní vyřešenou cache.

Použití:
    python scripts/export_okres_csv.py                    # -> data/callsign_okres.csv
    python scripts/export_okres_csv.py -o /tmp/export.csv
    python scripts/export_okres_csv.py --db data/hamstats.db
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, kraje  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("export_okres")

COLUMNS = ["callsign", "okres", "okres_lau", "kraj", "kraj_nuts", "kraj_iso"]


def export(db_path: Path, out_path: Path) -> tuple[int, int]:
    """Zapíše CSV. Vrací (počet_zapsaných, počet_nenapojených)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT callsign, okres FROM callsign_okres "
        "WHERE okres IS NOT NULL ORDER BY callsign"
    ).fetchall()

    written = 0
    unmapped: dict[str, int] = {}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            info = kraje.lookup(r["okres"])
            if info is None:
                # Nikdy tiše nezahazovat – zalogovat a přeskočit.
                unmapped[r["okres"]] = unmapped.get(r["okres"], 0) + 1
                continue
            w.writerow([
                r["callsign"], info.okres, info.lau,
                info.kraj.nazev, info.kraj.nuts, info.kraj.iso,
            ])
            written += 1

    skipped = sum(unmapped.values())
    if unmapped:
        log.warning("Nenapojené okresy (přeskočeny, doplň alias v app/kraje.py):")
        for name, cnt in sorted(unmapped.items(), key=lambda x: -x[1]):
            log.warning("  %r: %d značek", name, cnt)
    return written, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=config.DB_PATH,
                    help="cesta k SQLite DB (default: %(default)s)")
    ap.add_argument("-o", "--out", type=Path,
                    default=config.DATA_DIR / "callsign_okres.csv",
                    help="výstupní CSV (default: %(default)s)")
    args = ap.parse_args()

    if not args.db.exists():
        log.error("DB neexistuje: %s", args.db)
        sys.exit(1)

    written, skipped = export(args.db, args.out)
    log.info("Zapsáno %d značek do %s", written, args.out)
    if skipped:
        log.warning("Přeskočeno %d značek s neznámým okresem (viz výše).", skipped)


if __name__ == "__main__":
    main()
