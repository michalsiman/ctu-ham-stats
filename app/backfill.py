"""Jednorázový import historických CSV exportů ČTÚ do DB.

Historii nelze zpětně dohnat z živého zdroje (CSV nemá datum vydání), ale máme
starší ručně stažené exporty. Tento skript je uloží jako snapshoty pomocí
stejné cesty jako denní ingest (`ingest.store_snapshot`), takže se plní i
tabulky `licenses`/`callsigns` a diff se dopočítá standardně.

Spouštět chronologicky od nejstaršího a vždy tak, aby `snapshot_date` byl
menší než libovolný už uložený snapshot – jinak se `added/removed` u okolních
dat rozbijou (viz varování níže).

Spuštění:
    python -m app.backfill data/backfill/opravneni_2022-12-15.csv 2022-12-15
    python -m app.backfill data/backfill/opravneni_2025-06-06.csv 2025-06-06
"""
import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from . import db, ingest, stats

log = logging.getLogger(__name__)


def backfill(csv_path: Path, snapshot_date: date, assume_yes: bool = False) -> dict:
    """Naimportuje jeden historický CSV soubor jako snapshot k danému datu."""
    content = csv_path.read_text(encoding="utf-8")
    rows = ingest.parse_rows(content)  # oddělovač se autodetekuje
    if not rows:
        raise SystemExit(f"CSV {csv_path} neobsahuje žádné platné řádky – končím.")

    conn = db.connect()
    try:
        latest = stats.latest_snapshot(conn)
        snap = snapshot_date.isoformat()
        if latest is not None and snap < latest:
            log.warning(
                "Snapshot %s je starší než nejnovější uložený snapshot %s. "
                "Backfill „doprostřed/před“ existující historii nechá added/removed u "
                "novějších snapshotů neaktuální a first_seen v tabulce callsigns "
                "nesprávné (licenses se plní idempotentně, ta zůstane v pořádku). "
                "Backfill spouštějte chronologicky od nejstaršího a před prvním "
                "reálným ingestem.",
                snap, latest,
            )
            if not assume_yes and not _confirm():
                raise SystemExit("Přerušeno uživatelem.")

        result = ingest.store_snapshot(conn, rows, snapshot_date)
    finally:
        conn.close()

    log.info("Backfill hotov: %s", result)
    return result


def _confirm() -> bool:
    try:
        return input("Přesto pokračovat? [y/N] ").strip().lower() in ("y", "yes", "ano", "a")
    except (EOFError, OSError):
        # neinteraktivní běh (roura, CI) → nepotvrzeno
        return False


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Import historického CSV exportu ČTÚ.")
    parser.add_argument("csv_path", type=Path, help="cesta k CSV souboru")
    parser.add_argument("snapshot_date", type=date.fromisoformat, help="datum snapshotu YYYY-MM-DD")
    parser.add_argument("--yes", action="store_true", help="nevyžadovat potvrzení varování")
    args = parser.parse_args(argv)

    if not args.csv_path.is_file():
        parser.error(f"soubor {args.csv_path} neexistuje")

    result = backfill(args.csv_path, args.snapshot_date, assume_yes=args.yes)
    print(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main(sys.argv[1:])
