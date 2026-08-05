"""Dotazy nad uloženými snapshoty."""
import sqlite3
from datetime import date, timedelta


def latest_snapshot(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT snapshot_date FROM daily_stats ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    return row["snapshot_date"] if row else None


def _previous_month_start(current_month_start: date) -> date:
    if current_month_start.month == 1:
        return date(current_month_start.year - 1, 12, 1)
    return date(current_month_start.year, current_month_start.month - 1, 1)


def _latest_snapshot_in_range(
    conn: sqlite3.Connection, start: date, end: date
) -> str | None:
    row = conn.execute(
        """
        SELECT snapshot_date
        FROM daily_stats
        WHERE snapshot_date >= ? AND snapshot_date < ?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    return row["snapshot_date"] if row else None


def _callsign_delta_between_snapshots(
    conn: sqlite3.Connection, newer_snapshot: str, older_snapshot: str
) -> tuple[int, int]:
    added = conn.execute(
        """
        SELECT COUNT(*) AS n FROM (
            SELECT callsign
            FROM callsigns
            WHERE first_seen <= ? AND last_seen >= ?
            EXCEPT
            SELECT callsign
            FROM callsigns
            WHERE first_seen <= ? AND last_seen >= ?
        )
        """,
        (newer_snapshot, newer_snapshot, older_snapshot, older_snapshot),
    ).fetchone()["n"]
    removed = conn.execute(
        """
        SELECT COUNT(*) AS n FROM (
            SELECT callsign
            FROM callsigns
            WHERE first_seen <= ? AND last_seen >= ?
            EXCEPT
            SELECT callsign
            FROM callsigns
            WHERE first_seen <= ? AND last_seen >= ?
        )
        """,
        (older_snapshot, older_snapshot, newer_snapshot, newer_snapshot),
    ).fetchone()["n"]
    return added, removed


def summary(conn: sqlite3.Connection) -> dict | None:
    """Aktuální stav přehledů postavených na unikátních značkách."""
    latest = latest_snapshot(conn)
    if not latest:
        return None
    stats = conn.execute(
        "SELECT * FROM daily_stats WHERE snapshot_date = ?", (latest,)
    ).fetchone()
    monthly = monthly_change(conn)
    germany = conn.execute(
        "SELECT value FROM app_state WHERE key = ?",
        ("germany_callsigns_total",),
    ).fetchone()
    return {
        "snapshot_date": latest,
        "fetched_at": stats["fetched_at"],
        "unique_callsigns": stats["unique_callsigns"],
        "added": stats["added"],
        "removed": stats["removed"],
        "expiring_7": expiring_count(conn, 7),
        "expiring_30": expiring_count(conn, 30),
        "expiring_90": expiring_count(conn, 90),
        "germany_callsigns_total": int(germany["value"]) if germany else None,
        "monthly_added": monthly["added"] if monthly else None,
        "monthly_removed": monthly["removed"] if monthly else None,
        "unattended": len(station_list(conn, "unattended")),
        "special": len(station_list(conn, "special")),
        "clubs": len(station_list(conn, "club")),
    }


def expiring_count(conn: sqlite3.Connection, days: int) -> int | None:
    """Počet značek, jejichž poslední platnost končí do `days` dnů.

    Bere se max(valid_until) na značku (prodloužení = nový řádek s pozdějším datem).
    """
    latest = latest_snapshot(conn)
    if not latest:
        return None
    horizon = (date.today() + timedelta(days=days)).isoformat()
    today = date.today().isoformat()
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM (
            SELECT callsign, MAX(valid_until) AS max_valid
            FROM licenses
            WHERE last_seen = ?
            GROUP BY callsign
        )
        WHERE max_valid >= ? AND max_valid <= ?
        """,
        (latest, today, horizon),
    ).fetchone()
    return row["n"]


def monthly_change(conn: sqlite3.Connection) -> dict | None:
    """Změna mezi dvěma posledními kompletními měsíci podle koncových snapshotů."""
    if not latest_snapshot(conn):
        return None

    today = date.today()
    current_month_start = date(today.year, today.month, 1)
    prev_month_start = _previous_month_start(current_month_start)
    before_prev_month_start = _previous_month_start(prev_month_start)

    newer_snapshot = _latest_snapshot_in_range(conn, prev_month_start, current_month_start)
    older_snapshot = _latest_snapshot_in_range(
        conn, before_prev_month_start, prev_month_start
    )
    if not newer_snapshot or not older_snapshot:
        return None

    added, removed = _callsign_delta_between_snapshots(conn, newer_snapshot, older_snapshot)
    return {
        "snapshot_date": newer_snapshot,
        "compare_to": older_snapshot,
        "added": added,
        "removed": removed,
    }


def expiring_list(conn: sqlite3.Connection, days: int, limit: int = 500) -> list[dict]:
    """Seznam značek s blížící se expirací, seřazený podle data."""
    latest = latest_snapshot(conn)
    if not latest:
        return []
    horizon = (date.today() + timedelta(days=days)).isoformat()
    today = date.today().isoformat()
    rows = conn.execute(
        """
        SELECT callsign, MAX(valid_until) AS valid_until
        FROM licenses
        WHERE last_seen = ?
        GROUP BY callsign
        HAVING valid_until >= ? AND valid_until <= ?
        ORDER BY valid_until, callsign
        LIMIT ?
        """,
        (latest, today, horizon, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def daily_series(conn: sqlite3.Connection, limit: int = 365) -> list[dict]:
    """Časová řada denních statistik pro graf (vzestupně)."""
    rows = conn.execute(
        """
        SELECT snapshot_date, unique_callsigns, added, removed
        FROM daily_stats
        ORDER BY snapshot_date DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def callsign_lookup(conn: sqlite3.Connection, callsign: str) -> dict:
    """Vyhledání volací značky v aktuálních i historických snapshotech.

    Stavy:
      - not_found  – značka se v datech nikdy neobjevila (od začátku sběru)
      - active     – je v posledním snapshotu a platnost neuplynula
      - lapsed     – je v posledním snapshotu, ale platnost už uplynula
      - historical – v minulosti existovala, v aktuálním snapshotu už není
    """
    callsign = callsign.strip().upper()
    latest = latest_snapshot(conn)
    rows = conn.execute(
        """
        SELECT reference, valid_until, first_seen, last_seen
        FROM licenses
        WHERE callsign = ?
        ORDER BY valid_until DESC, first_seen
        """,
        (callsign,),
    ).fetchall()

    if not rows:
        return {"callsign": callsign, "status": "not_found"}

    records = [dict(r) for r in rows]
    max_valid = max(r["valid_until"] for r in records)
    in_latest = latest is not None and any(r["last_seen"] == latest for r in records)
    today = date.today().isoformat()

    if in_latest:
        status = "active" if max_valid >= today else "lapsed"
    else:
        status = "historical"

    return {
        "callsign": callsign,
        "status": status,
        "valid_until": max_valid,
        "expires_in_days": (date.fromisoformat(max_valid) - date.today()).days,
        "first_seen": min(r["first_seen"] for r in records),
        "last_seen": max(r["last_seen"] for r in records),
        "records": records,
    }


import re

_CALLSIGN_RE = re.compile(r"^(OK|OL)(\d+)([A-Z]+)$")


def breakdown(conn: sqlite3.Connection) -> dict | None:
    """Rozložení aktuálních značek, odděleně pro OK a OL.

    - prefixes: celkové počty OK / OL / ostatní (nerozparsovatelné)
    - prefix_digit: počty podle čísla za prefixem (speciální značky
      mohou mít víceciferné číslo, např. OL700xxx)
    - suffix_length: počty podle délky suffixu, všechny vyskytující se délky
    """
    latest = latest_snapshot(conn)
    if not latest:
        return None
    rows = conn.execute(
        "SELECT DISTINCT callsign FROM licenses WHERE last_seen = ?", (latest,)
    ).fetchall()

    prefixes: dict[str, int] = {}
    prefix_digit: dict[str, dict[str, int]] = {"OK": {}, "OL": {}}
    suffix_length: dict[str, dict[int, int]] = {"OK": {}, "OL": {}}

    for r in rows:
        m = _CALLSIGN_RE.match(r["callsign"])
        if not m:
            prefixes["ostatní"] = prefixes.get("ostatní", 0) + 1
            continue
        prefix, digits, suffix = m.groups()
        prefixes[prefix] = prefixes.get(prefix, 0) + 1
        pd = prefix_digit[prefix]
        pd[digits] = pd.get(digits, 0) + 1
        sl = suffix_length[prefix]
        sl[len(suffix)] = sl.get(len(suffix), 0) + 1

    def sort_digit(d: dict[str, int]) -> dict[str, int]:
        return {k: d[k] for k in sorted(d, key=lambda x: (len(x), x))}

    return {
        "snapshot_date": latest,
        "total": len(rows),
        "prefixes": dict(sorted(prefixes.items())),
        "prefix_digit": {p: sort_digit(v) for p, v in prefix_digit.items()},
        "suffix_length": {
            p: {str(k): v[k] for k in sorted(v)} for p, v in suffix_length.items()
        },
    }


def station_list(conn: sqlite3.Connection, kind: str) -> list[dict]:
    """Seznam značek daného druhu, abecedně, s max. platností na značku.

    kind:
      - "unattended" – neobsluhovaná zařízení (převaděče, majáky…):
                       prefix OK a číslo přesně 0
      - "special"    – speciální (příležitostné) značky:
                       víceciferné číslo za prefixem, např. OL700KLADNO
      - "club"       – klubové stanice dle vyhl. 155/2005 Sb.:
                       OK1/OK2 + tři písmena začínající K, O nebo R
    """
    latest = latest_snapshot(conn)
    if not latest:
        return []
    rows = conn.execute(
        """
        SELECT callsign, MAX(valid_until) AS valid_until
        FROM licenses WHERE last_seen = ?
        GROUP BY callsign ORDER BY callsign
        """,
        (latest,),
    ).fetchall()

    def match(callsign: str) -> bool:
        m = _CALLSIGN_RE.match(callsign)
        if not m:
            return False
        prefix, digits, suffix = m.groups()
        if kind == "unattended":
            return prefix == "OK" and digits == "0"
        if kind == "special":
            return len(digits) >= 2
        if kind == "club":
            return (prefix == "OK" and digits in ("1", "2")
                    and len(suffix) == 3 and suffix[0] in "KOR")
        return False

    return [dict(r) for r in rows if match(r["callsign"])]
