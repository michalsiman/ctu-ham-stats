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

def test_backfill_refuses_insert_into_middle(tmp_path, monkeypatch):
    dbp = tmp_path / "bf.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)
    seed = db.connect(dbp)
    store_snapshot(seed, [("OK1AAA", 1, "2030-01-01")], date(2026, 1, 1))
    seed.close()

    hist = tmp_path / "hist.csv"
    hist.write_text(_SEMI, encoding="utf-8")

    # datum >= nejstarší uložený snapshot (2026-01-01) → v neinteraktivním
    # režimu bez --yes se potvrzení nezíská a backfill se přeruší
    with pytest.raises(SystemExit):
        backfill.backfill(hist, date(2026, 6, 1))


def test_backfill_accepts_older_date(tmp_path, monkeypatch):
    dbp = tmp_path / "bf.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)
    seed = db.connect(dbp)
    store_snapshot(seed, [("OK1AAA", 1, "2030-01-01")], date(2026, 1, 1))
    seed.close()

    hist = tmp_path / "hist.csv"
    hist.write_text(_SEMI, encoding="utf-8")

    result = backfill.backfill(hist, date(2025, 1, 1))  # starší než earliest → bez varování
    assert result["snapshot_date"] == "2025-01-01"
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
