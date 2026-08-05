"""Denní ingest: stáhne CSV z ČTÚ, archivuje ho a uloží snapshot do DB.

Spuštění ručně:  python -m app.ingest
"""
import csv
import hashlib
import io
import logging
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from . import config, db

log = logging.getLogger(__name__)


def download_csv(url: str = config.CSV_URL) -> str:
    """Stáhne CSV a vrátí jeho obsah jako text."""
    resp = httpx.get(url, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def parse_germany_callsigns_total(content: str) -> int:
    """Vytáhne aktuální součet GESAMT ze stránky 12db statistik."""
    match = re.search(r"GESAMT\s+([\d.]+)", content, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        raise ValueError("GESAMT not found")
    return int(match.group(1).replace(".", ""))


def fetch_germany_callsigns_total(url: str = config.DE_RUFZEICHEN_STATS_URL) -> int:
    """Stáhne aktuální počet německých personengebundených značek."""
    resp = httpx.get(url, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return parse_germany_callsigns_total(resp.text)


def archive_csv(content: str, snapshot_date: date,
                stamp: str | None = None) -> Path | None:
    """Uloží surové CSV do archivu (source of truth, umožní přepočet).

    Při více bězích denně se ukládá jen tehdy, když se obsah oproti
    poslednímu archivu daného dne skutečně změnil – jinak by přibývaly
    identické kopie. Vrací cestu, nebo None při nezměněných datech.
    """
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    day = snapshot_date.isoformat()
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

    existing = sorted(config.ARCHIVE_DIR.glob(f"opravneni_{day}*.csv"))
    for path in existing:
        old = hashlib.sha256(path.read_bytes()).hexdigest()
        if old == digest:
            log.info("CSV se od %s nezměnilo – archiv se neduplikuje", path.name)
            return None

    stamp = stamp or datetime.now().strftime("%H%M")
    path = config.ARCHIVE_DIR / f"opravneni_{day}_{stamp}.csv"
    path.write_text(content, encoding="utf-8")
    return path


def parse_rows(content: str) -> list[tuple[str, int, str]]:
    """Vrátí seznam (callsign, reference, valid_until_iso_date).

    Sloupce CSV: ID, "Volací značka", "Číslo reference", "Platnost do"
    """
    rows: list[tuple[str, int, str]] = []
    reader = csv.DictReader(io.StringIO(content))
    for raw in reader:
        try:
            callsign = (raw.get("Volací značka") or "").strip().upper()
            reference = int((raw.get("Číslo reference") or "").strip())
            valid_until = (raw.get("Platnost do") or "").strip()[:10]  # ořízne čas
            # validace data
            date.fromisoformat(valid_until)
            if not callsign:
                raise ValueError("empty callsign")
        except (ValueError, TypeError) as exc:
            log.warning("Přeskakuji vadný řádek %r (%s)", raw, exc)
            continue
        rows.append((callsign, reference, valid_until))
    return rows


def store_snapshot(
    conn: sqlite3.Connection,
    rows: list[tuple[str, int, str]],
    snapshot_date: date,
) -> dict:
    """Uloží snapshot a spočítá denní diff unikátních značek.

    `added/removed` vyjadřuje změnu množiny volacích značek mezi snapshoty,
    nikoli změnu jednotlivých licenčních řádků.
    """
    snap = snapshot_date.isoformat()

    prev = conn.execute(
        "SELECT snapshot_date FROM daily_stats WHERE snapshot_date < ? "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (snap,),
    ).fetchone()
    prev_date = prev["snapshot_date"] if prev else None

    with conn:
        # upsert záznamů: nové dostanou first_seen, existující jen posunou last_seen
        conn.executemany(
            """
            INSERT INTO licenses (callsign, reference, valid_until, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(callsign, reference, valid_until)
            DO UPDATE SET last_seen = excluded.last_seen
            """,
            [(c, r, v, snap, snap) for c, r, v in rows],
        )

        unique_set = sorted({c for c, _, _ in rows})
        conn.executemany(
            """
            INSERT INTO callsigns (callsign, first_seen, last_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(callsign)
            DO UPDATE SET last_seen = excluded.last_seen
            """,
            [(c, snap, snap) for c in unique_set],
        )

        total_rows = len(rows)
        unique_callsigns = len(unique_set)

        added = removed = None
        if prev_date:
            added = conn.execute(
                "SELECT COUNT(*) AS n FROM callsigns WHERE first_seen = ?", (snap,)
            ).fetchone()["n"]
            removed = conn.execute(
                "SELECT COUNT(*) AS n FROM callsigns WHERE last_seen = ?", (prev_date,)
            ).fetchone()["n"]

        conn.execute(
            """
            INSERT INTO daily_stats
                (snapshot_date, total_rows, unique_callsigns, added, removed, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date) DO UPDATE SET
                total_rows = excluded.total_rows,
                unique_callsigns = excluded.unique_callsigns,
                added = excluded.added,
                removed = excluded.removed,
                fetched_at = excluded.fetched_at
            """,
            (
                snap,
                total_rows,
                unique_callsigns,
                added,
                removed,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )

    return {
        "snapshot_date": snap,
        "total_rows": total_rows,
        "unique_callsigns": unique_callsigns,
        "added": added,
        "removed": removed,
    }


def run_ingest(snapshot_date: date | None = None) -> dict:
    """Kompletní běh: stáhnout → archivovat → naparsovat → uložit."""
    snapshot_date = snapshot_date or date.today()
    content = download_csv()
    archive_csv(content, snapshot_date)
    rows = parse_rows(content)
    if not rows:
        raise RuntimeError("CSV neobsahuje žádné platné řádky – přerušuji ingest.")
    conn = db.connect()
    try:
        result = store_snapshot(conn, rows, snapshot_date)
        try:
            germany_total = fetch_germany_callsigns_total()
        except Exception:  # noqa: BLE001
            log.exception("Nepodařilo se načíst statistiku 12db")
        else:
            db.set_state(
                conn,
                "germany_callsigns_total",
                str(germany_total),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
    finally:
        conn.close()
    log.info("Ingest hotov: %s", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(run_ingest())
