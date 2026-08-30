"""Dotazy nad uloženými snapshoty."""
import re
import sqlite3
import unicodedata
from datetime import date, timedelta
from itertools import combinations, product

# Standardní OK/OL značka: jedna číslice a 1–4 písmen v příponě.
# Příležitostné/eventové značky mívají víc číslic nebo delší/číselnou příponu
# (např. OL70OU, OL15SOTA, OL22YOTA) – ty se defaultně vylučují.
_STANDARD_CALLSIGN_RE = re.compile(r"^(OK|OL)\d[A-Z]{1,4}$")


def _add_years(d: date, years: int) -> date:
    """Přičte roky s ošetřením 29. února (v nepřestupném roce → 28. 2.)."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year + years)


def latest_snapshot(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT snapshot_date FROM daily_stats ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    return row["snapshot_date"] if row else None


def earliest_snapshot(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT snapshot_date FROM daily_stats ORDER BY snapshot_date ASC LIMIT 1"
    ).fetchone()
    return row["snapshot_date"] if row else None


def normalize_suggestion_seed(text: str) -> str:
    """Převede libovolný text na posloupnost velkých písmen A-Z bez diakritiky."""
    normalized = unicodedata.normalize("NFKD", text.upper())
    return "".join(ch for ch in normalized if "A" <= ch <= "Z")


def _word_initials(text: str) -> str:
    initials = []
    for part in text.split():
        seed = normalize_suggestion_seed(part)
        if seed:
            initials.append(seed[0])
    return "".join(initials)


def _tokenized_words(text: str) -> list[str]:
    return [seed for part in text.split() if (seed := normalize_suggestion_seed(part))]


def _word_pattern_bases(text: str) -> list[str]:
    """Vytvoří krátké kandidáty z kombinací slov v původním pořadí.

    Cíl je dostat i varianty typu:
    - první písmena všech slov
    - první dvě písmena prvního slova + první písmeno posledního
    - první písmeno prvního + první dvě písmena druhého
    """
    words = _tokenized_words(text)
    if not words:
        return []

    bases: list[str] = []

    def add(candidate: str) -> None:
        candidate = candidate[:3]
        if candidate and candidate not in bases:
            bases.append(candidate)

    # Jednoslovné varianty: první 1-3 písmena každého slova.
    for word in words:
        for length in (1, 2, 3):
            if len(word) >= length:
                add(word[:length])

    if len(words) >= 2:
        first, last = words[0], words[-1]
        second = words[1]

        # Kombinace, které lidi běžně tvoří ručně.
        add(first[0] + second[0] + last[0])
        add(first[:2] + last[0])
        add(first[0] + second[:2])
        add(first[:2] + second[0])
        add(first[0] + last[:2])
        add(first[:2] + last[:1])

        # Pokud je slov víc, zkus i všechny inicály v pořadí.
        add(_word_initials(text))

        # Kombinace z libovolných sousedních slov, aby se text skutečně míchal.
        for idx in range(len(words) - 1):
            left, right = words[idx], words[idx + 1]
            add(left[0] + right[0])
            add(left[:2] + right[0])
            add(left[0] + right[:2])
            add(left[:2] + right[:1])
            add(left[:1] + right[:2])

        # Kombinace z prvního a posledního slova.
        add(first[0] + last[0])
        add(first[:2] + last[0])
        add(first[0] + last[:2])

    if len(words) >= 3:
        middle = words[1]
        add(first[0] + middle[0] + last[0])
        add(first[:2] + middle[0])
        add(first[0] + middle[:2])
        add(middle[:2] + last[0])
        add(middle[0] + last[:2])

        # Vyzkoušej i všechny trojice slov v původním pořadí.
        for start in range(len(words) - 2):
            a, b, c = words[start], words[start + 1], words[start + 2]
            add(a[0] + b[0] + c[0])
            add(a[:2] + b[0] + c[0])
            add(a[0] + b[:2] + c[0])
            add(a[0] + b[0] + c[:2])
            add(a[:2] + b[:1] + c[:1])
            add(a[:1] + b[:2] + c[:1])
            add(a[:1] + b[:1] + c[:2])

    return bases


def _candidate_suffixes(text: str, limit: int = 200) -> list[str]:
    """Vrátí kandidátní suffixy o délce 1-3 znaků v deterministickém pořadí."""
    bases = []
    normalized = normalize_suggestion_seed(text)
    if normalized:
        bases.append(normalized)

    initials = _word_initials(text)
    if initials and initials not in bases:
        bases.append(initials)

    for pattern in _word_pattern_bases(text):
        if pattern and pattern not in bases:
            bases.append(pattern)

    consonants = "".join(ch for ch in normalized if ch not in "AEIOUY")
    if consonants and consonants not in bases:
        bases.append(consonants)

    reversed_normalized = normalized[::-1]
    if reversed_normalized and reversed_normalized not in bases:
        bases.append(reversed_normalized)

    seen: set[str] = set()
    suffixes: list[str] = []

    def add(candidate: str) -> None:
        if 1 <= len(candidate) <= 3 and candidate not in seen:
            seen.add(candidate)
            suffixes.append(candidate)

    for base in bases:
        if not base:
            continue
        for length in (3, 2, 1):
            if len(base) >= length:
                add(base[:length])
                add(base[-length:])
                for start in range(len(base) - length + 1):
                    add(base[start:start + length])
                    if len(suffixes) >= limit:
                        return suffixes

        source = base[:8]
        for length in (3, 2, 1):
            if len(source) >= length:
                for indexes in combinations(range(len(source)), length):
                    add("".join(source[i] for i in indexes))
                    if len(suffixes) >= limit:
                        return suffixes

    return suffixes


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
    cz_pop = conn.execute(
        "SELECT value FROM app_state WHERE key = ?",
        ("cz_population",),
    ).fetchone()
    cz_population = int(cz_pop["value"]) if cz_pop else None
    unique = stats["unique_callsigns"]
    cz_penetration_pct = round(unique / cz_population * 100, 4) if cz_population else None
    de_pop = conn.execute(
        "SELECT value FROM app_state WHERE key = ?",
        ("de_population",),
    ).fetchone()
    de_population = int(de_pop["value"]) if de_pop else None
    germany_total = int(germany["value"]) if germany else None
    de_penetration_pct = round(germany_total / de_population * 100, 4) if (germany_total and de_population) else None
    return {
        "snapshot_date": latest,
        "fetched_at": stats["fetched_at"],
        "unique_callsigns": unique,
        "new_30": new_callsigns_count(conn, 30),
        "added": added,
        "removed": removed,
        "expiring_7": expiring_count(conn, 7),
        "expiring_30": expiring_count(conn, 30),
        "expiring_90": expiring_count(conn, 90),
        "germany_callsigns_total": germany_total,
        "cz_population": cz_population,
        "cz_penetration_pct": cz_penetration_pct,
        "de_population": de_population,
        "de_penetration_pct": de_penetration_pct,
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
    baseline = earliest_snapshot(conn)
    if not latest or not baseline:
        return None
    start, end = _new_callsigns_window(latest, days)
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM callsigns
        WHERE first_seen >= ?
          AND first_seen <= ?
          AND first_seen > ?
        """,
        (start, end, baseline),
    ).fetchone()
    return row["n"]


def new_callsigns_list(conn: sqlite3.Connection, days: int, limit: int = 500) -> list[dict]:
    """Seznam nově vzniklých značek za posledních `days` dní."""
    latest = latest_snapshot(conn)
    baseline = earliest_snapshot(conn)
    if not latest or not baseline:
        return []
    start, end = _new_callsigns_window(latest, days)
    rows = conn.execute(
        """
        SELECT
            c.callsign,
            c.first_seen,
            MAX(l.valid_until) AS valid_until
        FROM callsigns c
                JOIN licenses l ON l.callsign = c.callsign
        WHERE c.first_seen >= ?
          AND c.first_seen <= ?
                    AND c.first_seen > ?
        GROUP BY c.callsign, c.first_seen
        ORDER BY c.first_seen DESC, c.callsign ASC
        LIMIT ?
        """,
                (start, end, baseline, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# stavy volnosti navrhované značky (od „nejvolnější“ po „může se vrátit“)
FREEDOM_NEVER = "never_used"          # v datech nikdy nebyla → jistě volná
FREEDOM_ELAPSED = "protection_elapsed"  # poslední platnost + N let ≤ dnes → pravděpodobně volná
FREEDOM_LAPSED = "recently_lapsed"    # ochranná lhůta ještě běží → původní držitel může obnovit


def _annotate_freedom(
    conn: sqlite3.Connection,
    suggestions: list[dict],
    protection_years: int = 5,
) -> list[dict]:
    """Ke každému návrhu doplní `freedom` (viz FREEDOM_*) podle historie v `licenses`.

    U značek s historií přidá i `last_valid_until` a `protection_ended`. Stejná
    výhrada jako u `freed_after_protection` – z open dat bez osobních údajů nejde
    odlišit pozdní obnovu původním držitelem od nového přidělení, proto jde jen
    o odhad, ne jistotu.
    """
    if not suggestions:
        return suggestions
    calls = [s["callsign"] for s in suggestions]
    placeholders = ",".join("?" * len(calls))
    history = {
        row["callsign"]: row["last_valid"]
        for row in conn.execute(
            f"SELECT callsign, MAX(valid_until) AS last_valid FROM licenses "
            f"WHERE callsign IN ({placeholders}) GROUP BY callsign",
            calls,
        ).fetchall()
    }
    today = date.today()
    for s in suggestions:
        last_valid = history.get(s["callsign"])
        if last_valid is None:
            s["freedom"] = FREEDOM_NEVER
        else:
            free_date = _add_years(date.fromisoformat(last_valid), protection_years)
            s["last_valid_until"] = last_valid
            s["protection_ended"] = free_date.isoformat()
            s["freedom"] = FREEDOM_ELAPSED if free_date <= today else FREEDOM_LAPSED
    return suggestions


def suggest_callsigns(
    conn: sqlite3.Connection,
    text: str,
    limit: int = 48,
    digit: str | None = None,
    prefix: str = "OK",
    protection_years: int = 5,
) -> dict:
    """Navrhne volné značky {prefix}{digit}{suffix} podle zadaného textu.

    Každý návrh nese `freedom` (viz FREEDOM_*) pro rozlišení skutečně volných
    značek od těch, u nichž ještě běží ochranná lhůta.
    """
    if prefix not in ("OK", "OL"):
        raise ValueError("prefix musí být OK nebo OL")

    latest = latest_snapshot(conn)
    if not latest:
        return {
            "input": text,
            "normalized": "",
            "digit_filter": digit,
            "prefix": prefix,
            "count": 0,
            "suggestions": [],
        }

    normalized = normalize_suggestion_seed(text)
    if not normalized:
        return {
            "input": text,
            "normalized": "",
            "digit_filter": digit,
            "prefix": prefix,
            "count": 0,
            "suggestions": [],
        }

    if digit is not None and (len(digit) != 1 or digit not in "0123456789"):
        return {
            "input": text,
            "normalized": normalized,
            "digit_filter": digit,
            "prefix": prefix,
            "count": 0,
            "suggestions": [],
        }

    current_callsigns = {
        row["callsign"]
        for row in conn.execute(
            "SELECT callsign FROM callsigns WHERE last_seen = ?",
            (latest,),
        ).fetchall()
    }

    raw_suffixes = _candidate_suffixes(text)
    short_suffixes = [s for s in raw_suffixes if len(s) <= 2]
    long_suffixes = [s for s in raw_suffixes if len(s) == 3]
    ordered_suffixes = short_suffixes + long_suffixes

    available_by_suffix: list[tuple[str, list[str]]] = []
    for suffix in ordered_suffixes:
        digits_pool = [digit] if digit else list("1234567890")
        digits = [d for d in digits_pool if f"{prefix}{d}{suffix}" not in current_callsigns]
        if digits:
            available_by_suffix.append((suffix, digits))

    suggestions: list[dict] = []

    def _finish() -> dict:
        _annotate_freedom(conn, suggestions, protection_years)
        return {
            "input": text,
            "normalized": normalized,
            "digit_filter": digit,
            "prefix": prefix,
            "count": len(suggestions),
            "suggestions": suggestions,
        }

    round_index = 0
    while len(suggestions) < limit:
        progressed = False
        for suffix, digits in available_by_suffix:
            if round_index >= len(digits):
                continue
            digit = digits[round_index]
            suggestions.append(
                {
                    "callsign": f"{prefix}{digit}{suffix}",
                    "digit": digit,
                    "suffix": suffix,
                }
            )
            progressed = True
            if len(suggestions) >= limit:
                return _finish()
        if not progressed:
            break
        round_index += 1

    return _finish()


def suggest_contest_callsigns(
    conn: sqlite3.Connection,
    prefix: str = "OK",
    digit: str | None = None,
    limit: int = 260,
    protection_years: int = 5,
) -> dict:
    """Vypíše volné závodní značky tvaru {prefix}{číslice}{1 písmeno}.

    Na rozdíl od `suggest_callsigns` se neodvozuje z textu, ale enumeruje
    kombinace `PREFIX + číslice + 1 písmeno` a vrací jen ty, které nejsou v
    posledním snapshotu (tedy pravděpodobně volné short cally pro závody).

    U prefixu `OK` je číslice 0 vyhrazená pro klubové/speciální stanice, takže
    `OK0` + 1 písmeno neexistuje a vylučuje se; u `OL` je `OL0` + 1 písmeno
    platné, proto se 0 povoluje.
    """
    if prefix not in ("OK", "OL"):
        raise ValueError("prefix musí být OK nebo OL")

    latest = latest_snapshot(conn)
    if not latest:
        return {"prefix": prefix, "digit_filter": digit, "contest": True,
                "count": 0, "suggestions": []}

    if digit is not None and (len(digit) != 1 or digit not in "0123456789"):
        return {"prefix": prefix, "digit_filter": digit, "contest": True,
                "count": 0, "suggestions": []}

    current_callsigns = {
        row["callsign"]
        for row in conn.execute(
            "SELECT callsign FROM callsigns WHERE last_seen = ?",
            (latest,),
        ).fetchall()
    }

    # u OK je 0 pro klubové/speciální stanice (OK0+1 písmeno neexistuje), u OL je platná
    all_digits = list("0123456789") if prefix == "OL" else list("123456789")
    digits = [d for d in ([digit] if digit else all_digits) if d in all_digits]
    letters = [chr(c) for c in range(ord("A"), ord("Z") + 1)]

    suggestions: list[dict] = []

    def _finish() -> dict:
        _annotate_freedom(conn, suggestions, protection_years)
        return {"prefix": prefix, "digit_filter": digit, "contest": True,
                "count": len(suggestions), "suggestions": suggestions}

    for d in digits:
        for letter in letters:
            callsign = f"{prefix}{d}{letter}"
            if callsign in current_callsigns:
                continue
            suggestions.append({"callsign": callsign, "digit": d, "suffix": letter})
            if len(suggestions) >= limit:
                return _finish()

    return _finish()


_A_TO_Z = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
_SUFFIX_DIGIT_ORDER = "1234567890"  # 1–9 pak 0 (0 bývá klubové/speciální)


def _suffixes_containing(fragment: str, max_len: int = 3) -> list[str]:
    """Všechny přípony délky ≤ max_len obsahující `fragment` jako souvislý úsek.

    Např. 'AA' → ['AA', 'AAA', 'AAB', …, 'BAA', …] (přesně, začíná i končí).
    """
    n = len(fragment)
    if n == 0 or n > max_len:
        return []
    out: set[str] = {fragment}
    for total in range(n + 1, max_len + 1):
        extra = total - n
        for before in range(extra + 1):
            after = extra - before
            for pre in product(_A_TO_Z, repeat=before):
                for suf in product(_A_TO_Z, repeat=after):
                    out.add("".join(pre) + fragment + "".join(suf))
    return sorted(out, key=lambda s: (len(s), s))


def suggest_by_suffix_contains(
    conn: sqlite3.Connection,
    text: str,
    prefix: str = "OK",
    digit: str | None = None,
    limit: int = 48,
    protection_years: int = 5,
) -> dict:
    """Volné značky, jejichž přípona (do 3 písmen) OBSAHUJE zadaný text.

    Text se bere jako souvislý úsek přípony, takže se najdou značky, které jím
    začínají i končí (např. 'AA' → OK1AA, OK1AAB, OK1BAA). Každý návrh nese
    `freedom` stejně jako ostatní návrhy.
    """
    if prefix not in ("OK", "OL"):
        raise ValueError("prefix musí být OK nebo OL")

    normalized = normalize_suggestion_seed(text)
    latest = latest_snapshot(conn)
    invalid_digit = digit is not None and (len(digit) != 1 or digit not in "0123456789")
    if not latest or not normalized or len(normalized) > 3 or invalid_digit:
        return {"input": text, "normalized": normalized, "fragment": normalized,
                "prefix": prefix, "digit_filter": digit, "mode": "suffix_contains",
                "count": 0, "suggestions": []}

    current_callsigns = {
        row["callsign"]
        for row in conn.execute(
            "SELECT callsign FROM callsigns WHERE last_seen = ?",
            (latest,),
        ).fetchall()
    }

    candidate_suffixes = _suffixes_containing(normalized, 3)
    digits = [digit] if digit else list(_SUFFIX_DIGIT_ORDER)

    suggestions: list[dict] = []
    for suffix in candidate_suffixes:
        for d in digits:
            callsign = f"{prefix}{d}{suffix}"
            if callsign in current_callsigns:
                continue
            suggestions.append({"callsign": callsign, "digit": d, "suffix": suffix})

    # kratší přípony první, pak číslice v pořadí 1–9,0, pak abecedně
    suggestions.sort(key=lambda s: (len(s["suffix"]), _SUFFIX_DIGIT_ORDER.index(s["digit"]), s["suffix"]))
    suggestions = suggestions[:limit]
    _annotate_freedom(conn, suggestions, protection_years)

    return {"input": text, "normalized": normalized, "fragment": normalized,
            "prefix": prefix, "digit_filter": digit, "mode": "suffix_contains",
            "count": len(suggestions), "suggestions": suggestions}


def freed_after_protection(
    conn: sqlite3.Connection,
    protection_years: int = 5,
    include_occasional: bool = False,
) -> list[dict]:
    """Značky nepřítomné v posledním snapshotu, kde od posledního známého
    'Platnost do' uplynulo aspoň `protection_years` let.

    DŮLEŽITÁ VÝHRADA – vracet vždy s polem `confidence: "candidate"`, nikdy
    netvrdit jistotu. ČTÚ open data neobsahují osobní údaje, nelze odlišit
    pozdní obnovu původním držitelem od přidělení novému zájemci (pozorovali
    jsme značky, které zmizely na 1–3 roky a pak se vrátily s novou referencí).
    """
    latest = latest_snapshot(conn)
    if not latest:
        return []

    rows = conn.execute(
        """
        SELECT callsign, MAX(valid_until) AS last_valid
        FROM licenses
        WHERE callsign NOT IN (
            SELECT callsign FROM callsigns WHERE last_seen = ?
        )
        GROUP BY callsign
        """,
        (latest,),
    ).fetchall()

    today = date.today()
    out: list[dict] = []
    for row in rows:
        callsign = row["callsign"]
        if not include_occasional and not _STANDARD_CALLSIGN_RE.match(callsign):
            continue
        last_valid = date.fromisoformat(row["last_valid"])
        free_date = _add_years(last_valid, protection_years)
        if free_date <= today:
            out.append({
                "callsign": callsign,
                "last_valid_until": last_valid.isoformat(),
                "protection_ended": free_date.isoformat(),
                "confidence": "candidate",
            })
    out.sort(key=lambda r: r["protection_ended"])
    return out


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


# Mezera mezi po sobě jdoucími snapshoty větší než tento počet dní znamená, že
# added/removed pokrývá dlouhé období (backfill starších importů nebo první reálný
# ingest po nich) – taková „denní“ delta by křivku zkreslila, proto se do grafu
# nekreslí a bod se označí jako reconstructed.
_RECONSTRUCTED_GAP_DAYS = 2


def daily_series(conn: sqlite3.Connection, limit: int = 365) -> list[dict]:
    """Časová řada denních statistik pro graf (vzestupně).

    U bodů s velkou mezerou k předchozímu snapshotu se added/removed vynuluje
    (viz `_RECONSTRUCTED_GAP_DAYS`), aby jednorázový přeskok z backfillu
    nezkreslil denní přírůstkovou křivku. Počty unikátních značek zůstávají.
    """
    rows = conn.execute(
        """
        SELECT snapshot_date, unique_callsigns, added, removed
        FROM daily_stats
        ORDER BY snapshot_date DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    series = [dict(r) for r in reversed(rows)]
    prev_date: date | None = None
    for point in series:
        current = date.fromisoformat(point["snapshot_date"])
        reconstructed = (
            prev_date is not None
            and (current - prev_date).days > _RECONSTRUCTED_GAP_DAYS
        )
        point["reconstructed"] = reconstructed
        if reconstructed:
            point["added"] = None
            point["removed"] = None
        prev_date = current
    return series


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
    other_examples: list[str] = []

    for r in rows:
        m = _CALLSIGN_RE.match(r["callsign"])
        if not m:
            prefixes["ostatní"] = prefixes.get("ostatní", 0) + 1
            other_examples.append(r["callsign"])
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
        "other_examples": sorted(other_examples),
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
