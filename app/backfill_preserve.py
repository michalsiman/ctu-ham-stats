#!/usr/bin/env python3
"""Doplní historické CSV exporty ČTÚ do BĚŽÍCÍ DB, aniž sáhne na živá data.

Proč tenhle skript a ne `app.backfill`:
`app.backfill` (přes `ingest.store_snapshot`) při vložení STARŠÍHO data do DB,
kde už jsou novější snapshoty, posune `last_seen` dozadu a rozbil by tak
poslední nasbíraný měsíc. Ten na serveru existuje JEN v DB (raw CSV v
`data/archive/` chybí), takže „smazat DB a postavit znovu“ (BACKFILL.md, postup
A) tady nejde – přišli bychom o něj.

Tenhle skript proto:
  1. udělá bezpečnou zálohu DB (VACUUM INTO – konzistentní i při WAL),
  2. vloží staré exporty jako čistě historické snapshoty:
     - licenses: INSERT s `first_seen = MIN(first_seen, datum exportu)`; u řádků,
       které v živé DB už jsou, se OPRAVÍ jen `first_seen` dozadu (licence
       existovala dřív), `last_seen` se NIKDY nemění → živá data zůstávají,
     - daily_stats: nový řádek s added/removed = NULL (graf ho kvůli mezeře
       stejně vykreslí jako reconstructed),
  3. přebuduje odvozenou tabulku `callsigns` z `licenses` (first_seen = MIN,
     last_seen = MAX) → opraví se i stáří starých značek,
  4. (volitelně `--ingest`) dotáhne dnešek denním ingestem.

Je idempotentní – opakované spuštění nic nerozbije. Nesahá na `daily_stats` ani
`last_seen` živého období. Nespouštět společně s postupem A z BACKFILL.md.

Spuštění (z kořene repa, dvojice CSV + datum, chronologicky od nejstaršího):
    python -m app.backfill_preserve \\
        data/backfill/import_radiove_kmitocty_opravneni-122022.csv 2022-12-15 \\
        data/backfill/import_radiove_kmitocty_opravneni06062025.csv 2025-06-06
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import config, db, ingest

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_historical_snapshot(
    conn: sqlite3.Connection,
    rows: list[tuple[str, int, str]],
    snap_date: date,
) -> dict:
    """Vloží jeden starý export jako historický snapshot bez zásahu do živých dat.

    `last_seen` existujících řádků se nemění; `first_seen` se jen sníží dozadu,
    pokud starý export dokládá dřívější existenci licence.
    """
    snap = snap_date.isoformat()

    # Pojistka: nechceme vkládat datum, které už spadá do období pokrytého
    # denním ingestem (added/removed spočítané = reálný denní snapshot).
    first_live = conn.execute(
        "SELECT MIN(snapshot_date) AS m FROM daily_stats "
        "WHERE added IS NOT NULL OR removed IS NOT NULL"
    ).fetchone()["m"]
    if first_live is not None and snap >= first_live:
        raise SystemExit(
            f"Chyba: {snap} už spadá do období pokrytého denním ingestem "
            f"(od {first_live}). Tenhle skript je jen pro STARŠÍ exporty."
        )

    unique = sorted({c for c, _, _ in rows})
    with conn:
        conn.executemany(
            """
            INSERT INTO licenses
                (callsign, reference, valid_until, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(callsign, reference, valid_until) DO UPDATE SET
                first_seen = MIN(first_seen, excluded.first_seen)
            """,
            [(c, r, v, snap, snap) for c, r, v in rows],
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO daily_stats
                (snapshot_date, total_rows, unique_callsigns, added, removed, fetched_at)
            VALUES (?, ?, ?, NULL, NULL, ?)
            """,
            (snap, len(rows), len(unique), _now()),
        )

    return {"snapshot_date": snap, "total_rows": len(rows),
            "unique_callsigns": len(unique)}


def rebuild_callsigns(conn: sqlite3.Connection) -> int:
    """Přebuduje `callsigns` z `licenses` (zdroj pravdy jsou intervaly licencí)."""
    with conn:
        conn.execute("DELETE FROM callsigns")
        conn.execute(
            """
            INSERT INTO callsigns (callsign, first_seen, last_seen)
            SELECT callsign, MIN(first_seen), MAX(last_seen)
            FROM licenses
            GROUP BY callsign
            """
        )
    return conn.execute("SELECT COUNT(*) AS n FROM callsigns").fetchone()["n"]


def backup_db(db_path: Path) -> Path:
    """Konzistentní záloha DB do jednoho souboru (funguje i při zapnutém WAL)."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    src = sqlite3.connect(db_path)
    try:
        src.execute("VACUUM INTO ?", (str(bak),))
    finally:
        src.close()
    return bak


def _print_daily(conn: sqlite3.Connection, title: str) -> None:
    print(f"\n{title}")
    for r in conn.execute(
        "SELECT snapshot_date, unique_callsigns, added, removed "
        "FROM daily_stats ORDER BY snapshot_date"
    ):
        print(f"  {r['snapshot_date']}  uniq={r['unique_callsigns']:>5}  "
              f"added={r['added']}  removed={r['removed']}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("pairs", nargs="+", metavar="CSV DATUM",
                   help="dvojice: cesta k CSV exportu a datum snapshotu (YYYY-MM-DD)")
    p.add_argument("--no-backup", action="store_true",
                   help="nevytvářet zálohu DB před zásahem (nedoporučeno)")
    p.add_argument("--ingest", action="store_true",
                   help="po dokončení spustit denní ingest (dotáhne dnešek)")
    args = p.parse_args(argv)

    if len(args.pairs) % 2 != 0:
        p.error("očekávám dvojice CSV DATUM – počet argumentů musí být sudý")

    items: list[tuple[Path, date]] = []
    for i in range(0, len(args.pairs), 2):
        csv_path = Path(args.pairs[i])
        try:
            snap = date.fromisoformat(args.pairs[i + 1])
        except ValueError:
            p.error(f"neplatné datum: {args.pairs[i + 1]}")
        if not csv_path.is_file():
            p.error(f"soubor neexistuje: {csv_path}")
        items.append((csv_path, snap))

    db_path = config.DB_PATH
    if not args.no_backup:
        bak = backup_db(db_path)
        print(f"záloha DB: {bak}")

    conn = db.connect()
    try:
        _print_daily(conn, "PŘED:")
        # Historické snapshoty chronologicky od nejstaršího.
        for csv_path, snap in sorted(items, key=lambda it: it[1]):
            rows = ingest.parse_rows(csv_path.read_text(encoding="utf-8"))
            if not rows:
                raise SystemExit(f"CSV {csv_path} nemá žádné platné řádky – končím.")
            res = add_historical_snapshot(conn, rows, snap)
            log.info("Vložen historický snapshot %s: %s", csv_path.name, res)

        n = rebuild_callsigns(conn)
        log.info("callsigns přebudováno: %s značek", n)
        _print_daily(conn, "PO:")
    finally:
        conn.close()

    if args.ingest:
        result = ingest.run_ingest()
        log.info("Ingest hotov: %s", result)

    print("\nHotovo. Poslední nasbíraný měsíc zůstal beze změny; "
          "další denní ingest pokračuje standardně.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
