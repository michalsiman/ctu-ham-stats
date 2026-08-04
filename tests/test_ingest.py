"""Testy ingestu a statistik nad dvěma simulovanými dny."""
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from app import db, stats
from app.ingest import parse_rows, store_snapshot

FIXTURES = Path(__file__).parent


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    yield conn
    conn.close()


def load(name: str) -> list:
    return parse_rows((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_skips_bad_rows_and_normalizes():
    rows = load("sample_day1.csv")
    assert len(rows) == 6  # vadný řádek přeskočen
    callsigns = {c for c, _, _ in rows}
    assert "OK1CCC" in callsigns  # ořezané mezery
    assert all(len(v) == 10 for _, _, v in rows)  # čas oříznut na datum


def test_first_snapshot_has_no_diff(conn):
    result = store_snapshot(conn, load("sample_day1.csv"), date(2026, 8, 1))
    assert result["total_rows"] == 6
    assert result["unique_callsigns"] == 5
    assert result["added"] is None and result["removed"] is None


def test_second_snapshot_diff(conn):
    store_snapshot(conn, load("sample_day1.csv"), date(2026, 8, 1))
    result = store_snapshot(conn, load("sample_day2.csv"), date(2026, 8, 2))
    # přidáno: OK9NEW + prodloužená OK1BBB (nové datum platnosti)
    assert result["added"] == 2
    # zmizelo: OK1CCC + stará platnost OK1BBB
    assert result["removed"] == 2


def test_ingest_is_idempotent(conn):
    store_snapshot(conn, load("sample_day1.csv"), date(2026, 8, 1))
    store_snapshot(conn, load("sample_day2.csv"), date(2026, 8, 2))
    result = store_snapshot(conn, load("sample_day2.csv"), date(2026, 8, 2))
    assert result["added"] == 2 and result["removed"] == 2


def test_expiring_uses_max_validity_per_callsign(conn, monkeypatch):
    store_snapshot(conn, load("sample_day2.csv"), date(2026, 8, 2))

    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 3)

    monkeypatch.setattr(stats, "date", FakeDate)
    # OK1AAA expiruje 2026-08-31; OK1DDD má sice řádek 2026-08-15,
    # ale max platnost 2031 → nepočítá se
    assert stats.expiring_count(conn, 30) == 1
    listed = stats.expiring_list(conn, 90)
    assert [r["callsign"] for r in listed] == ["OK1AAA"]


def test_summary(conn, monkeypatch):
    store_snapshot(conn, [
        ("OK1A", 1, "2030-01-31"),
        ("OK1B", 2, "2030-01-31"),
        ("OK1C", 3, "2030-01-31"),
    ], date(2026, 6, 30))
    store_snapshot(conn, [
        ("OK1A", 1, "2030-01-31"),
        ("OK1C", 3, "2030-01-31"),
        ("OK1D", 4, "2030-01-31"),
    ], date(2026, 7, 31))
    store_snapshot(conn, [
        ("OK1A", 1, "2030-01-31"),
        ("OK1C", 3, "2030-01-31"),
        ("OK1D", 4, "2030-01-31"),
        ("OK1E", 5, "2026-08-10"),
        ("OK1F", 6, "2026-08-20"),
    ], date(2026, 8, 2))

    from app import stats as stats_module
    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 4)

    monkeypatch.setattr(stats_module, "date", FakeDate)
    s = stats.summary(conn)
    assert s["snapshot_date"] == "2026-08-02"
    assert s["unique_callsigns"] == 5
    assert s["expiring_7"] == 1
    assert s["monthly_added"] == 1
    assert s["monthly_removed"] == 1


def test_callsign_lookup_states(conn, monkeypatch):
    store_snapshot(conn, load("sample_day1.csv"), date(2026, 8, 1))
    store_snapshot(conn, load("sample_day2.csv"), date(2026, 8, 2))

    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 3)

    monkeypatch.setattr(stats, "date", FakeDate)

    active = stats.callsign_lookup(conn, "ok1ddd ")  # normalizace vstupu
    assert active["status"] == "active"
    assert active["valid_until"] == "2031-06-30"
    assert len(active["records"]) == 2

    gone = stats.callsign_lookup(conn, "OK1CCC")  # zmizela mezi snapshoty
    assert gone["status"] == "historical"
    assert gone["last_seen"] == "2026-08-01"

    assert stats.callsign_lookup(conn, "OK9XYZ")["status"] == "not_found"


def test_breakdown(conn):
    store_snapshot(conn, load("sample_day2.csv"), date(2026, 8, 2))
    b = stats.breakdown(conn)
    # den 2: OK1AAA, OK1BBB, OK1DDD, OK0RRR, OK9NEW → vše OK
    assert b["prefixes"] == {"OK": 5}
    assert b["prefix_digit"]["OK"] == {"0": 1, "1": 3, "9": 1}
    assert b["prefix_digit"]["OL"] == {}
    assert b["suffix_length"]["OK"] == {"3": 5}
    assert b["total"] == 5


def test_breakdown_special_callsigns(conn):
    from app.ingest import store_snapshot as ss
    rows = [
        ("OK1AB", 1, "2030-01-31"),
        ("OL700KLADNO", 2, "2030-01-31"),   # víceciferné číslo, 6písmenný suffix
        ("OL5X", 3, "2030-01-31"),
        ("OM1XX", 4, "2030-01-31"),         # cizí prefix → ostatní
    ]
    ss(conn, rows, date(2026, 8, 2))
    b = stats.breakdown(conn)
    assert b["prefixes"] == {"OK": 1, "OL": 2, "ostatní": 1}
    assert b["prefix_digit"]["OL"] == {"5": 1, "700": 1}
    assert b["suffix_length"]["OL"] == {"1": 1, "6": 1}


def test_breakdown_counts_renewed_callsign_once(conn):
    """Prodloužená značka = víc řádků v tabulce, ale v breakdownu jen 1×."""
    rows = [
        ("OK1A", 1, "2026-09-30"),   # původní oprávnění
        ("OK1A", 1, "2031-09-30"),   # prodloužení (stejná ref, nové datum)
        ("OK1A", 99, "2030-01-31"),  # druhé oprávnění téže značky
        ("OK1B", 2, "2030-01-31"),
    ]
    store_snapshot(conn, rows, date(2026, 8, 2))
    b = stats.breakdown(conn)
    assert b["total"] == 2                       # jen OK1A a OK1B
    assert b["suffix_length"]["OK"] == {"1": 2}  # ne 4!
    assert b["prefix_digit"]["OK"] == {"1": 2}


def test_breakdown_ignores_historical_callsigns(conn):
    """Značka, která zmizela ze snapshotu, se do rozložení nepočítá."""
    store_snapshot(conn, [("OK1A", 1, "2030-01-31"), ("OK1B", 2, "2030-01-31")],
                   date(2026, 8, 1))
    store_snapshot(conn, [("OK1A", 1, "2030-01-31")], date(2026, 8, 2))
    b = stats.breakdown(conn)
    assert b["total"] == 1
    assert b["suffix_length"]["OK"] == {"1": 1}


def test_station_lists(conn):
    rows = [
        ("OK0BAB", 1, "2030-01-31"),     # neobsluhovaná
        ("OK0BAB", 1, "2031-01-31"),     # prodloužení – nesmí zdvojit
        ("OK0X", 2, "2030-01-31"),       # neobsluhovaná
        ("OK1AAA", 3, "2030-01-31"),     # běžná
        ("OL700KLADNO", 4, "2026-12-31"),# speciální
        ("OK100Y", 5, "2026-12-31"),     # speciální (OK, 3 číslice)
        ("OL5X", 6, "2030-01-31"),       # běžná OL – není speciální
    ]
    store_snapshot(conn, rows, date(2026, 8, 2))

    un = stats.station_list(conn, "unattended")
    assert [s["callsign"] for s in un] == ["OK0BAB", "OK0X"]
    assert un[0]["valid_until"] == "2031-01-31"  # max platnost po prodloužení

    sp = stats.station_list(conn, "special")
    assert [s["callsign"] for s in sp] == ["OK100Y", "OL700KLADNO"]

    s = stats.summary(conn)
    assert s["unattended"] == 2 and s["special"] == 2


def test_club_stations(conn):
    rows = [
        ("OK1KCR", 1, "2030-01-31"),  # klub (K)
        ("OK2OZL", 2, "2030-01-31"),  # klub (O)
        ("OK1RAJ", 3, "2030-01-31"),  # klub (R)
        ("OK1RA", 4, "2030-01-31"),   # jen 2 písmena → jednotlivec
        ("OK5KWW", 5, "2030-01-31"),  # číslice 5 → jednotlivec
        ("OK1ABC", 6, "2030-01-31"),  # nezačíná K/O/R → jednotlivec
        ("OK0KRC", 7, "2030-01-31"),  # OK0 → neobsluhovaná, ne klub
    ]
    store_snapshot(conn, rows, date(2026, 8, 2))
    clubs = stats.station_list(conn, "club")
    assert [c["callsign"] for c in clubs] == ["OK1KCR", "OK1RAJ", "OK2OZL"]
    assert stats.summary(conn)["clubs"] == 3


def test_ingest_times_parsing(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "INGEST_TIMES", "06:00,14:00")
    assert config.ingest_times() == [(6, 0), (14, 0)]
    monkeypatch.setattr(config, "INGEST_TIMES", " 5:30 , 12 ,, 22:45 ")
    assert config.ingest_times() == [(5, 30), (12, 0), (22, 45)]
    monkeypatch.setattr(config, "INGEST_TIMES", "")
    assert config.ingest_times() == [(6, 0)]  # fallback


def test_archive_skips_identical_content(tmp_path, monkeypatch):
    from app import config, ingest as ing
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path)
    content = "ID,x\n1,y\n"
    first = ing.archive_csv(content, date(2026, 8, 2), stamp="0600")
    assert first is not None and first.name == "opravneni_2026-08-02_0600.csv"
    # stejný obsah odpoledne → neukládá se znovu
    assert ing.archive_csv(content, date(2026, 8, 2), stamp="1400") is None
    # změněný obsah → nový soubor vedle původního
    second = ing.archive_csv(content + "2,z\n", date(2026, 8, 2), stamp="1400")
    assert second is not None
    assert len(list(tmp_path.glob("*.csv"))) == 2


def test_second_run_same_day_updates_diff(conn):
    """Odpolední běh přepíše denní statistiku, diff zůstává proti předchozímu dni."""
    store_snapshot(conn, load("sample_day1.csv"), date(2026, 8, 1))
    morning = store_snapshot(conn, load("sample_day2.csv"), date(2026, 8, 2))
    afternoon_rows = load("sample_day2.csv") + [("OK1NEW", 100009, "2031-12-31")]
    afternoon = store_snapshot(conn, afternoon_rows, date(2026, 8, 2))

    assert afternoon["unique_callsigns"] == morning["unique_callsigns"] + 1
    assert afternoon["added"] == morning["added"] + 1
    # jen jeden řádek za den, ne dva
    n = conn.execute("SELECT COUNT(*) AS n FROM daily_stats").fetchone()["n"]
    assert n == 2


def test_summary_exposes_fetch_time(conn):
    store_snapshot(conn, load("sample_day1.csv"), date(2026, 8, 1))
    s = stats.summary(conn)
    assert s["fetched_at"].endswith("+00:00")   # UTC ISO timestamp


def test_repeated_run_updates_fetch_time(conn):
    store_snapshot(conn, load("sample_day1.csv"), date(2026, 8, 1))
    first = stats.summary(conn)["fetched_at"]
    import time
    time.sleep(1.1)
    store_snapshot(conn, load("sample_day1.csv"), date(2026, 8, 1))
    assert stats.summary(conn)["fetched_at"] > first
