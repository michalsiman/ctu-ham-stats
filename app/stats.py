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


def daily_delta_details(conn: sqlite3.Connection) -> dict | None:
    """Detail denní změny: které značky mezi posledními 2 snapshoty přibyly/ubyly."""
    latest = latest_snapshot(conn)
    if not latest:
        return None

    prev = conn.execute(
        "SELECT snapshot_date FROM daily_stats WHERE snapshot_date < ? "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (latest,),
    ).fetchone()
    prev_snapshot = prev["snapshot_date"] if prev else None
    if not prev_snapshot:
        return {
            "snapshot_date": latest,
            "compare_to": None,
            "added": [],
            "removed": [],
            "added_count": 0,
            "removed_count": 0,
            "net": 0,
        }

    added_rows = conn.execute(
        """
        WITH
            newer AS (
                SELECT callsign
                FROM callsigns
                WHERE first_seen <= ? AND last_seen >= ?
            ),
            older AS (
                SELECT callsign
                FROM callsigns
                WHERE first_seen <= ? AND last_seen >= ?
            ),
            added AS (
                SELECT callsign FROM newer
                EXCEPT
                SELECT callsign FROM older
            )
        SELECT a.callsign, MAX(l.valid_until) AS valid_until
        FROM added a
        JOIN licenses l
            ON l.callsign = a.callsign
           AND l.first_seen <= ? AND l.last_seen >= ?
        GROUP BY a.callsign
        ORDER BY a.callsign
        """,
        (latest, latest, prev_snapshot, prev_snapshot, latest, latest),
    ).fetchall()
    removed_rows = conn.execute(
        """
        WITH
            newer AS (
                SELECT callsign
                FROM callsigns
                WHERE first_seen <= ? AND last_seen >= ?
            ),
            older AS (
                SELECT callsign
                FROM callsigns
                WHERE first_seen <= ? AND last_seen >= ?
            ),
            removed AS (
                SELECT callsign FROM older
                EXCEPT
                SELECT callsign FROM newer
            )
        SELECT r.callsign, MAX(l.valid_until) AS valid_until
        FROM removed r
        JOIN licenses l
            ON l.callsign = r.callsign
           AND l.first_seen <= ? AND l.last_seen >= ?
        GROUP BY r.callsign
        ORDER BY r.callsign
        """,
        (latest, latest, prev_snapshot, prev_snapshot, prev_snapshot, prev_snapshot),
    ).fetchall()

    added = [dict(r) for r in added_rows]
    removed = [dict(r) for r in removed_rows]
    return {
        "snapshot_date": latest,
        "compare_to": prev_snapshot,
        "added": added,
        "removed": removed,
        "added_count": len(added),
        "removed_count": len(removed),
        "net": len(added) - len(removed),
    }


def summary(conn: sqlite3.Connection) -> dict | None:
    """Aktuální stav přehledů postavených na unikátních značkách."""
    latest = latest_snapshot(conn)
    if not latest:
        return None
    stats = conn.execute(
        "SELECT * FROM daily_stats WHERE snapshot_date = ?", (latest,)
    ).fetchone()
    prev = conn.execute(
        "SELECT snapshot_date FROM daily_stats WHERE snapshot_date < ? "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (latest,),
    ).fetchone()
    prev_snapshot = prev["snapshot_date"] if prev else None
    if prev_snapshot:
        added, removed = _callsign_delta_between_snapshots(conn, latest, prev_snapshot)
    else:
        added = removed = None

    monthly = monthly_change(conn)
    germany = conn.execute(
        "SELECT value FROM app_state WHERE key = ?",
        ("germany_callsigns_total",),
    ).fetchone()
    return {
        "snapshot_date": latest,
        "fetched_at": stats["fetched_at"],
        "unique_callsigns": stats["unique_callsigns"],
        "new_30": new_callsigns_count(conn, 30),
        "added": added,
        "removed": removed,
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


def _new_callsigns_window(latest: str, days: int) -> tuple[str, str]:
    end = date.fromisoformat(latest)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def new_callsigns_count(conn: sqlite3.Connection, days: int) -> int | None:
    """Počet značek, které se poprvé objevily v posledních `days` dnech.

    Jde o nové záznamy v tabulce unikátních značek (`callsigns`),
    tedy podle data `first_seen` bez ohledu na to, zda jsou dnes aktivní.
    Prodloužení existující značky se sem nedostane, protože nemění `first_seen`.
    """
    latest = latest_snapshot(conn)
    if not latest:
        return None
    start, end = _new_callsigns_window(latest, days)
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM callsigns
        WHERE first_seen >= ?
          AND first_seen <= ?
        """,
                (start, end),
    ).fetchone()
    return row["n"]


def new_callsigns_list(conn: sqlite3.Connection, days: int, limit: int = 500) -> list[dict]:
    """Seznam nově vzniklých značek za posledních `days` dní."""
    latest = latest_snapshot(conn)
    if not latest:
        return []
    start, end = _new_callsigns_window(latest, days)
    rows = conn.execute(
        """
        SELECT
            c.callsign,
            c.first_seen,
            MAX(l.valid_until) AS valid_until
        FROM callsigns c
        JOIN licenses l
                    ON l.callsign = c.callsign
        WHERE c.first_seen >= ?
          AND c.first_seen <= ?
        GROUP BY c.callsign, c.first_seen
        ORDER BY c.first_seen DESC, c.callsign ASC
        LIMIT ?
        """,
                (start, end, limit),
    ).fetchall()
    return [dict(r) for r in rows]


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


def visit_stats_for_day(conn: sqlite3.Connection, day: str) -> dict:
    """Souhrn návštěv za konkrétní den včetně unikátů podle země."""
    totals = conn.execute(
        """
        SELECT
            COUNT(*) AS unique_visitors,
            COALESCE(SUM(hits), 0) AS hits
        FROM page_visits
        WHERE visited_on = ?
        """,
        (day,),
    ).fetchone()

    countries = conn.execute(
        """
        SELECT
            country_code,
            COUNT(*) AS unique_visitors,
            COALESCE(SUM(hits), 0) AS hits
        FROM page_visits
        WHERE visited_on = ?
        GROUP BY country_code
        ORDER BY unique_visitors DESC, hits DESC, country_code ASC
        """,
        (day,),
    ).fetchall()

    return {
        "day": day,
        "unique_visitors": totals["unique_visitors"],
        "hits": totals["hits"],
        "countries": [dict(row) for row in countries],
    }


def visit_stats_for_range(conn: sqlite3.Connection, days: int, end_day: str | None = None) -> dict:
    """Souhrn návštěv za posledních `days` dní (včetně koncového dne)."""
    end = date.fromisoformat(end_day) if end_day else date.today()
    start = end - timedelta(days=days - 1)
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    totals = conn.execute(
        """
        SELECT
            COUNT(DISTINCT visitor_hash) AS unique_visitors,
            COALESCE(SUM(hits), 0) AS hits
        FROM page_visits
        WHERE visited_on >= ? AND visited_on <= ?
        """,
        (start_iso, end_iso),
    ).fetchone()

    countries = conn.execute(
        """
        SELECT
            country_code,
            COUNT(DISTINCT visitor_hash) AS unique_visitors,
            COALESCE(SUM(hits), 0) AS hits
        FROM page_visits
        WHERE visited_on >= ? AND visited_on <= ?
        GROUP BY country_code
        ORDER BY unique_visitors DESC, hits DESC, country_code ASC
        """,
        (start_iso, end_iso),
    ).fetchall()

    daily = conn.execute(
        """
        SELECT
            visited_on AS day,
            COUNT(*) AS unique_visitors,
            COALESCE(SUM(hits), 0) AS hits
        FROM page_visits
        WHERE visited_on >= ? AND visited_on <= ?
        GROUP BY visited_on
        ORDER BY visited_on ASC
        """,
        (start_iso, end_iso),
    ).fetchall()

    return {
        "days": days,
        "start_day": start_iso,
        "end_day": end_iso,
        "unique_visitors": totals["unique_visitors"],
        "hits": totals["hits"],
        "countries": [dict(row) for row in countries],
        "daily": [dict(row) for row in daily],
    }
