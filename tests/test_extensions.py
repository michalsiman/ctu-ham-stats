"""Testy rozšíření: autodetekce oddělovače, backfill, návrhy OK/OL a závodní,
kandidáti na uvolnění po ochranné lhůtě."""
from datetime import date

import pytest

from app import backfill, config, db, stats
from app.ingest import parse_rows, store_snapshot

# stejná data, jen jiný oddělovač (starší export ČTÚ používá středník)
_COMMA = (
    'ID,"Volací značka","Číslo reference","Platnost do"\n'
    '1,OK1AAA,100001,"2030-01-31 00:00:00"\n'
    '2,OK1BBB,100002,"2031-06-30 00:00:00"\n'
)
_SEMI = (
    'ID;"Volací značka";"Číslo reference";"Platnost do"\n'
    '1;OK1AAA;100001;"2030-01-31 00:00:00"\n'
    '2;OK1BBB;100002;"2031-06-30 00:00:00"\n'
)


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    yield conn
    conn.close()


# --- 0. autodetekce oddělovače ---

def test_parse_rows_autodetects_delimiter():
    assert parse_rows(_COMMA) == parse_rows(_SEMI)
    assert parse_rows(_SEMI) == [
        ("OK1AAA", 100001, "2030-01-31"),
        ("OK1BBB", 100002, "2031-06-30"),
    ]


# --- 0. backfill ---

def test_backfill_refuses_date_before_latest(tmp_path, monkeypatch):
    dbp = tmp_path / "bf.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)
    seed = db.connect(dbp)
    store_snapshot(seed, [("OK1AAA", 1, "2030-01-01")], date(2026, 8, 30))
    seed.close()

    hist = tmp_path / "hist.csv"
    hist.write_text(_SEMI, encoding="utf-8")

    # datum starší než nejnovější uložený snapshot (2026-08-30) → „doprostřed“
    # historie; v neinteraktivním režimu bez --yes se potvrzení nezíská a přeruší se
    with pytest.raises(SystemExit):
        backfill.backfill(hist, date(2025, 6, 6))


def test_backfill_appends_newer_without_warning(tmp_path, monkeypatch):
    dbp = tmp_path / "bf.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)
    seed = db.connect(dbp)
    store_snapshot(seed, [("OK1AAA", 1, "2030-01-01")], date(2022, 12, 15))
    seed.close()

    hist = tmp_path / "hist.csv"
    hist.write_text(_SEMI, encoding="utf-8")

    # novější než latest → normální chronologický krok, bez varování
    result = backfill.backfill(hist, date(2025, 6, 6))
    assert result["snapshot_date"] == "2025-06-06"
    assert result["unique_callsigns"] == 2


# --- 1. návrhy OK/OL a závodní režim ---

def test_suggest_prefix_ol_returns_ol_without_collisions(conn):
    store_snapshot(conn, [("OL1NO", 1, "2030-01-01")], date(2026, 8, 1))
    r = stats.suggest_callsigns(conn, "Novak", 20, None, "OL")
    assert r["prefix"] == "OL"
    calls = {s["callsign"] for s in r["suggestions"]}
    assert calls  # něco vzniklo
    assert all(c.startswith("OL") for c in calls)
    assert "OL1NO" not in calls  # obsazená se nenavrhne


def test_suggest_invalid_prefix_raises(conn):
    with pytest.raises(ValueError):
        stats.suggest_callsigns(conn, "x", prefix="XX")


def test_contest_lists_only_free_single_letter(conn):
    store_snapshot(conn, [("OK1A", 1, "2030-01-01"), ("OK1B", 2, "2030-01-01")], date(2026, 8, 1))
    r = stats.suggest_contest_callsigns(conn, "OK", "1")
    calls = {s["callsign"] for s in r["suggestions"]}
    assert r["contest"] is True
    assert "OK1A" not in calls and "OK1B" not in calls  # obsazené chybí
    assert "OK1C" in calls
    # formát: PREFIX + číslice + 1 písmeno
    assert all(len(c) == 4 and c[:2] == "OK" and c[2] == "1" and c[3].isalpha() for c in calls)


def test_contest_prefix_ol_and_digit_filter(conn):
    store_snapshot(conn, [("OK1AAA", 1, "2030-01-01")], date(2026, 8, 1))
    r = stats.suggest_contest_callsigns(conn, "OL", "5")
    calls = [s["callsign"] for s in r["suggestions"]]
    assert len(calls) == 26  # OL5A..OL5Z, nic obsazené
    assert all(c.startswith("OL5") for c in calls)


def test_contest_ok_excludes_digit_zero(conn):
    store_snapshot(conn, [("OK1A", 1, "2030-01-01")], date(2026, 8, 1))
    # OK0 + 1 písmeno neexistuje (klubové/speciální stanice)
    r = stats.suggest_contest_callsigns(conn, "OK", limit=1000)
    assert not any(s["callsign"].startswith("OK0") for s in r["suggestions"])
    assert r["count"] == 9 * 26 - 1  # 1..9 × A..Z, minus obsazené OK1A
    assert stats.suggest_contest_callsigns(conn, "OK", "0")["count"] == 0  # explicitní 0 → prázdno


def test_suffixes_containing_start_and_end():
    s = stats._suffixes_containing("AA")
    assert "AA" in s              # přesně
    assert "AAB" in s             # začíná AA
    assert "BAA" in s             # končí AA
    assert all("AA" in x and len(x) <= 3 for x in s)
    assert len(s) == 1 + 26 + 26 - 1  # AA + AA? + ?AA, mínus duplicitní AAA
    assert stats._suffixes_containing("ABC") == ["ABC"]  # 3 písmena → jen přesně
    assert stats._suffixes_containing("ABCD") == []      # >3 → nic


def test_suggest_by_suffix_contains(conn):
    store_snapshot(conn, [("OK1AA", 1, "2030-01-01")], date(2026, 8, 1))  # obsazená
    r = stats.suggest_by_suffix_contains(conn, "AA", "OK", None, limit=1000)
    calls = {s["callsign"] for s in r["suggestions"]}
    assert r["mode"] == "suffix_contains"
    assert all("AA" in s["suffix"] for s in r["suggestions"])  # každá přípona obsahuje AA
    assert "OK1AA" not in calls        # obsazená vynechána
    assert "OK2AA" in calls            # jiná číslice volná
    assert "OK1AAB" in calls           # začíná AA
    assert "OK1BAA" in calls           # končí AA
    assert all("freedom" in s for s in r["suggestions"])  # barva volnosti i tady
    # prefix OL a filtr číslice
    r5 = stats.suggest_by_suffix_contains(conn, "AA", "OL", "5", limit=1000)
    assert all(c["callsign"].startswith("OL5") for c in r5["suggestions"])
    # více než 3 písmena → prázdno
    assert stats.suggest_by_suffix_contains(conn, "ABCD", "OK")["count"] == 0


def test_suggestions_carry_freedom_status(conn):
    # OK1B nikdy nebyla; OK1C propadla nedávno (ochrana běží); OK1D dávno (po lhůtě)
    today = date.today()
    recent = today.replace(year=today.year - 1).isoformat()
    old = stats._add_years(today, -6).isoformat()
    store_snapshot(conn, [
        ("OK9ZZ", 1, "2035-01-01"),   # drží snapshot, je v latest
        ("OK1C", 2, recent),          # v historii, nedávno → recently_lapsed
        ("OK1D", 3, old),             # v historii, dávno → protection_elapsed
    ], date(2026, 8, 1))
    store_snapshot(conn, [("OK9ZZ", 1, "2035-01-01")], date(2026, 8, 2))  # OK1C/OK1D zmizely

    r = stats.suggest_contest_callsigns(conn, "OK", "1", limit=1000)
    by = {s["callsign"]: s for s in r["suggestions"]}
    assert by["OK1B"]["freedom"] == stats.FREEDOM_NEVER
    assert by["OK1C"]["freedom"] == stats.FREEDOM_LAPSED
    assert by["OK1C"]["protection_ended"]  # datum konce ochrany je vyplněné
    assert by["OK1D"]["freedom"] == stats.FREEDOM_ELAPSED


def test_contest_ol_allows_digit_zero(conn):
    store_snapshot(conn, [("OK1A", 1, "2030-01-01")], date(2026, 8, 1))
    # OL0 + 1 písmeno je platné
    r = stats.suggest_contest_callsigns(conn, "OL", limit=1000)
    calls = {s["callsign"] for s in r["suggestions"]}
    assert "OL0A" in calls
    assert r["count"] == 10 * 26  # 0..9 × A..Z, nic obsazené (OK1A je jiný prefix)
    r0 = stats.suggest_contest_callsigns(conn, "OL", "0")
    assert r0["count"] == 26 and all(c["callsign"].startswith("OL0") for c in r0["suggestions"])


# --- 2. kandidáti na uvolnění po ochranné lhůtě ---

def test_add_years_handles_leap_day():
    assert stats._add_years(date(2020, 2, 29), 5) == date(2025, 2, 28)
    assert stats._add_years(date(2021, 6, 30), 5) == date(2026, 6, 30)


def _seed_two_snapshots(conn, extra_day1):
    """1. snapshot obsahuje `extra_day1` navíc, 2. už ne (značky tak zmizí)."""
    base = [("OK9ABC", 1, "2000-01-01")]  # zůstává (mimo test), drží 2. snapshot neprázdný
    store_snapshot(conn, base + extra_day1, date(2026, 8, 1))
    store_snapshot(conn, base, date(2026, 8, 2))


def test_freed_returns_only_expired_standard_with_confidence(conn):
    today = date.today()
    long_ago = today.replace(year=today.year - 10).isoformat()   # dávno po 5leté lhůtě
    recent = today.replace(year=today.year - 1).isoformat()      # lhůta ještě neuplynula
    occasional = today.replace(year=today.year - 10).isoformat()

    _seed_two_snapshots(conn, [
        ("OK1FRE", 10, long_ago),     # standardní, dávno volná → vrátit
        ("OK2NEW", 11, recent),       # standardní, ale lhůta neuplynula → nevracet
        ("OL70OU", 12, occasional),   # příležitostná (2 číslice) → bez include_occasional nevracet
    ])

    freed = stats.freed_after_protection(conn, 5)
    calls = {r["callsign"] for r in freed}
    assert "OK1FRE" in calls
    assert "OK2NEW" not in calls
    assert "OL70OU" not in calls
    assert all(r["confidence"] == "candidate" for r in freed)

    freed_occ = stats.freed_after_protection(conn, 5, include_occasional=True)
    assert "OL70OU" in {r["callsign"] for r in freed_occ}


def test_longest_expired_orders_oldest_first_and_filters(conn):
    today = date.today()
    old = today.replace(year=today.year - 3).isoformat()   # vypršela dávno
    newer = today.replace(year=today.year - 1).isoformat()  # vypršela nedávno
    future = today.replace(year=today.year + 1).isoformat()  # zmizela, ale ještě platná

    _seed_two_snapshots(conn, [
        ("OK1OLD", 1, old),        # nejdéle expirovaná → první
        ("OK2NEW", 2, newer),      # expirovaná později
        ("OL70OU", 3, old),        # příležitostná → bez include_occasional vynechat
        ("OK3FUT", 4, future),     # platnost neuplynula → vynechat
    ])

    res = stats.longest_expired(conn)
    calls = [r["callsign"] for r in res]
    assert calls[0] == "OK1OLD"                 # nejstarší expirace nahoře
    assert calls.index("OK1OLD") < calls.index("OK2NEW")
    assert "OL70OU" not in calls                # příležitostná vynechána
    assert "OK3FUT" not in calls                # ještě platná vynechána
    assert res[0]["expired_days_ago"] > res[calls.index("OK2NEW")]["expired_days_ago"]

    with_occ = [r["callsign"] for r in stats.longest_expired(conn, include_occasional=True)]
    assert "OL70OU" in with_occ


def test_freed_edge_exactly_on_boundary(conn):
    today = date.today()
    boundary = stats._add_years(today, -5).isoformat()  # přesně 5 let zpět → free_date == dnes
    _seed_two_snapshots(conn, [("OK3EDG", 20, boundary)])
    freed = stats.freed_after_protection(conn, 5)
    assert "OK3EDG" in {r["callsign"] for r in freed}


# --- 4. anomální den v denní křivce ---

def test_daily_series_marks_reconstructed_on_gap(conn):
    rows = [("OK1AAA", 1, "2030-01-01")]
    for d in (date(2022, 12, 15), date(2025, 6, 6), date(2026, 8, 29), date(2026, 8, 30)):
        store_snapshot(conn, rows, d)
    series = {p["snapshot_date"]: p for p in stats.daily_series(conn)}
    assert series["2025-06-06"]["reconstructed"] is True
    assert series["2025-06-06"]["added"] is None
    assert series["2026-08-29"]["reconstructed"] is True
    assert series["2026-08-30"]["reconstructed"] is False
