#!/usr/bin/env python3
"""POC: dohledání OKRESU ke značkám přes callbooky (QRZ.com / HamQTH / QRZCQ).

Cíl: na náhodném vzorku aktivních značek změřit, kolika se podaří přiřadit okres,
a podle toho rozhodnout, jestli stavět choropleth mapu počtu koncesí po okresech.

PRIVACY-BY-DESIGN: jediný údaj, který nás zajímá a který se ukládá, je OKRES.
Odpovědi callbooků (jméno, adresa, PSČ, souřadnice, lokátor) se zpracují jen
v paměti – spočítá se z nich okres a vzápětí se zahodí. Do DB (tabulka
callsign_okres) jde výhradně dvojice značka→okres + nenosobní metadata.
Žádné jméno, adresa, PSČ, souřadnice ani lokátor se neukládají ani nelogují.
Klienti proto ani nenačítají jméno/ulici – jen pole nutná k odvození okresu.

Callbooky pro české (OK/OL) značky nevrací okres přímo (pole state/county/fips
jsou jen pro USA), proto se okres odvozuje z:
  - lat/lon      → point-in-polygon proti hranicím okresů (data/geo/okresy.geojson)
  - PSČ (zip)    → lookup tabulka PSČ→okres     (data/geo/psc_okres.csv)
  - město (addr2)→ lookup tabulka obec→okres    (data/geo/obce_okresy.csv)
  - grid         → střed lokátoru → lat/lon → point-in-polygon (fallback)

Chybějící referenční dataset = daná metoda se přeskočí (report ji označí N/A).

Spuštění:
    QRZ_USERNAME=... QRZ_PASSWORD=... python scripts/qrz_poc.py
    # nebo creds v config.local.ini (sekce [qrz]); parametry viz --help
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from collections import defaultdict
import sqlite3
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Umožní `python scripts/qrz_poc.py` i bez instalace balíčku.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db, okresy  # noqa: E402

log = logging.getLogger("qrz_poc")

QRZ_NS = "{http://xmldata.qrz.com}"
GEO_DIR = config.DATA_DIR / "geo"
OKRESY_GEOJSON = GEO_DIR / "okresy.geojson"
PSC_OKRES_CSV = GEO_DIR / "psc_okres.csv"
OBCE_OKRESY_CSV = GEO_DIR / "obce_okresy.csv"
REPORT_PATH = config.DATA_DIR / "qrz_poc_report.json"

# Pole potřebná POUZE k odvození okresu (zpracují se v paměti, neukládají se).
# Záměrně BEZ jména/ulice – ta k okresu nepotřebujeme, tak je vůbec nenačítáme.
CALLSIGN_FIELDS = ("addr2", "zip", "grid", "lat", "lon", "geoloc")


# --- QRZ XML klient ----------------------------------------------------------
class QRZError(Exception):
    pass


class QRZAuthError(QRZError):
    """Neplatný/vypršelý session key – je třeba se znovu přihlásit."""


def _parse_xml(text: str) -> ET.Element:
    """Naparsuje XML; nevalidní odpověď callbooku převede na QRZError
    (smyčka ji umí přeskočit místo pádu celého běhu)."""
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise QRZError(f"nevalidní XML z callbooku: {exc}") from exc


class QRZClient:
    """Minimalistický klient nad QRZ XML API (login + callsign lookup)."""

    def __init__(self, username: str, password: str, agent: str, url: str):
        self._username = username
        self._password = password
        self._agent = agent
        self._url = url
        self._session_key: str | None = None
        self._client = httpx.Client(timeout=30, follow_redirects=True)
        self.lookup_count = 0  # dle QRZ "Count" – dotazy za posledních 24 h

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "QRZClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @staticmethod
    def _text(elem: ET.Element | None, tag: str) -> str | None:
        if elem is None:
            return None
        node = elem.find(f"{QRZ_NS}{tag}")
        if node is None or node.text is None:
            return None
        val = node.text.strip()
        return val or None

    def _parse(self, xml_text: str) -> ET.Element:
        try:
            return ET.fromstring(xml_text)
        except ET.ParseError as exc:  # noqa: PERF203
            raise QRZError(f"nevalidní XML z QRZ: {exc}") from exc

    def login(self) -> None:
        if not self._username or not self._password:
            raise QRZError(
                "Chybí QRZ credentials. Nastav QRZ_USERNAME a QRZ_PASSWORD "
                "(env), nebo sekci [qrz] v config.local.ini."
            )
        resp = self._client.get(
            self._url,
            params={
                "username": self._username,
                "password": self._password,
                "agent": self._agent,
            },
        )
        resp.raise_for_status()
        root = self._parse(resp.text)
        session = root.find(f"{QRZ_NS}Session")
        error = self._text(session, "Error")
        key = self._text(session, "Key")
        if error and not key:
            raise QRZError(f"QRZ login selhal: {error}")
        if not key:
            raise QRZError("QRZ login nevrátil session key")
        self._session_key = key
        count = self._text(session, "Count")
        subexp = self._text(session, "SubExp")
        log.info("QRZ přihlášení OK (lookups za 24h: %s, subscription: %s)", count, subexp)

    def lookup(self, callsign: str) -> dict:
        """Vrátí dict s poli značky.

        Klíč "found": True/False; při nalezení i jednotlivá pole z CALLSIGN_FIELDS.
        Při vypršelé session automaticky obnoví přihlášení a zopakuje dotaz.
        """
        for attempt in (1, 2):
            if not self._session_key:
                self.login()
            resp = self._client.get(
                self._url, params={"s": self._session_key, "callsign": callsign}
            )
            resp.raise_for_status()
            root = self._parse(resp.text)
            session = root.find(f"{QRZ_NS}Session")
            error = self._text(session, "Error")
            count = self._text(session, "Count")
            if count and count.isdigit():
                self.lookup_count = int(count)

            if error:
                low = error.lower()
                if ("session timeout" in low or "invalid session" in low) and attempt == 1:
                    log.info("QRZ session vypršela, přihlašuji se znovu")
                    self._session_key = None
                    continue
                if "not found" in low or "no callsign" in low:
                    return {"found": False}
                raise QRZError(f"QRZ chyba u {callsign}: {error}")

            cs = root.find(f"{QRZ_NS}Callsign")
            if cs is None:
                return {"found": False}
            out: dict = {"found": True}
            for field in CALLSIGN_FIELDS:
                out[field] = self._text(cs, field)
            for coord in ("lat", "lon"):
                if out.get(coord) is not None:
                    try:
                        out[coord] = float(out[coord])
                    except ValueError:
                        out[coord] = None
            return out
        return {"found": False}


# HamQTH vrací u části záznamů default souřadnici (~střed ČR / Praha) místo
# skutečné polohy stanice. Takové coords zahazujeme, ať point-in-polygon
# nepřiřadí falešně Prahu; okres se pak vezme z gridu/PSČ/města.
HAMQTH_DEFAULT_LATLON = (50.07, 14.42)


def _is_hamqth_default(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    return abs(lat - HAMQTH_DEFAULT_LATLON[0]) < 0.005 and \
        abs(lon - HAMQTH_DEFAULT_LATLON[1]) < 0.02


class HamQTHClient:
    """Klient nad HamQTH XML API (login + callsign lookup). Session platí 1 h."""

    NS = "{https://www.hamqth.com}"
    # Jen pole nutná k odvození okresu (bez jména/ulice).
    FIELD_MAP = {
        "addr2": "adr_city",
        "zip": "adr_zip",
        "grid": "grid",
    }

    def __init__(self, username: str, password: str, url: str):
        self._username = username
        self._password = password
        self._url = url
        self._session_id: str | None = None
        self._client = httpx.Client(timeout=30, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HamQTHClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def enabled(self) -> bool:
        return bool(self._username and self._password)

    def _text(self, elem: ET.Element | None, tag: str) -> str | None:
        if elem is None:
            return None
        node = elem.find(f"{self.NS}{tag}")
        if node is None or node.text is None:
            return None
        val = node.text.strip()
        return val or None

    def login(self) -> None:
        if not self.enabled:
            raise QRZError("Chybí HamQTH credentials (sekce [hamqth] / env).")
        resp = self._client.get(
            self._url, params={"u": self._username, "p": self._password}
        )
        resp.raise_for_status()
        root = _parse_xml(resp.text)
        session = root.find(f"{self.NS}session")
        sid = self._text(session, "session_id")
        error = self._text(session, "error")
        if not sid:
            raise QRZError(f"HamQTH login selhal: {error or 'bez session_id'}")
        self._session_id = sid
        log.info("HamQTH přihlášení OK")

    def lookup(self, callsign: str) -> dict:
        for attempt in (1, 2):
            if not self._session_id:
                self.login()
            resp = self._client.get(
                self._url,
                params={"id": self._session_id, "callsign": callsign, "prg": "ctu-ham-stats"},
            )
            resp.raise_for_status()
            root = _parse_xml(resp.text)
            session = root.find(f"{self.NS}session")
            error = self._text(session, "error")
            if error and "session" in error.lower() and attempt == 1:
                self._session_id = None
                continue
            search = root.find(f"{self.NS}search")
            if search is None:
                return {"found": False}
            out: dict = {"found": True}
            for our, their in self.FIELD_MAP.items():
                out[our] = self._text(search, their)
            if not out.get("addr2"):
                out["addr2"] = self._text(search, "qth")
            out["district_code"] = self._text(search, "district")
            lat = self._text(search, "latitude")
            lon = self._text(search, "longitude")
            try:
                lat = float(lat) if lat else None
                lon = float(lon) if lon else None
            except ValueError:
                lat = lon = None
            if _is_hamqth_default(lat, lon):
                lat = lon = None  # default poloha – ignorovat
            out["lat"], out["lon"] = lat, lon
            out["geoloc"] = None
            return out
        return {"found": False}


class QRZCQClient:
    """Klient nad QRZCQ.com XML API (login + callsign lookup). Session platí 3 dny.

    Struktura je jako u QRZ (Session/Key, Callsign), jen jiný namespace a URL.
    Vyžaduje samostatný účet na qrzcq.com.
    """

    NS = "{http://qrzcq.com}"
    # Jen pole nutná k odvození okresu (bez jména/ulice).
    FIELD_MAP = {
        "addr2": "city",
        "zip": "zip",
        "grid": "locator",
    }

    def __init__(self, username: str, password: str, url: str):
        self._username = username
        self._password = password
        self._url = url
        self._session_key: str | None = None
        self._client = httpx.Client(timeout=30, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "QRZCQClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def enabled(self) -> bool:
        return bool(self._username and self._password)

    def _text(self, elem: ET.Element | None, tag: str) -> str | None:
        if elem is None:
            return None
        node = elem.find(f"{self.NS}{tag}")
        if node is None or node.text is None:
            return None
        val = node.text.strip()
        return val or None

    def login(self) -> None:
        if not self.enabled:
            raise QRZError("Chybí QRZCQ credentials (sekce [qrzcq] / env).")
        resp = self._client.get(
            self._url,
            params={"username": self._username, "password": self._password,
                    "agent": "ctu-ham-stats"},
        )
        resp.raise_for_status()
        root = _parse_xml(resp.text)
        session = root.find(f"{self.NS}Session")
        key = self._text(session, "Key")
        error = self._text(session, "Error")
        if not key:
            raise QRZError(f"QRZCQ login selhal: {error or 'bez klíče'}")
        self._session_key = key
        log.info("QRZCQ přihlášení OK")

    def lookup(self, callsign: str) -> dict:
        for attempt in (1, 2):
            if not self._session_key:
                self.login()
            resp = self._client.get(
                self._url,
                params={"s": self._session_key, "callsign": callsign, "agent": "ctu-ham-stats"},
            )
            resp.raise_for_status()
            root = _parse_xml(resp.text)
            session = root.find(f"{self.NS}Session")
            error = self._text(session, "Error")
            if error:
                low = error.lower()
                if ("session" in low or "timeout" in low) and attempt == 1:
                    self._session_key = None
                    continue
                if "not found" in low or "premium" in low:
                    return {"found": False}
                raise QRZError(f"QRZCQ chyba u {callsign}: {error}")
            cs = root.find(f"{self.NS}Callsign")
            if cs is None:
                return {"found": False}
            out: dict = {"found": True}
            for our, their in self.FIELD_MAP.items():
                out[our] = self._text(cs, their)
            for coord in ("latitude", "longitude"):
                v = self._text(cs, coord)
                key = "lat" if coord == "latitude" else "lon"
                try:
                    out[key] = float(v) if v else None
                except ValueError:
                    out[key] = None
            out["geoloc"] = None
            return out
        return {"found": False}


# --- Odvození okresu ---------------------------------------------------------
def _strip_diacritics(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in norm if not unicodedata.combining(ch))


def _norm_name(text: str) -> str:
    """Klíč pro porovnání názvů obcí: bez diakritiky, velká, jen A-Z0-9."""
    text = _strip_diacritics(text.strip().upper())
    return "".join(ch for ch in text if ch.isalnum())


def _norm_psc(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def grid_to_latlon(grid: str) -> tuple[float, float] | None:
    """Střed Maidenhead lokátoru (4 nebo 6 znaků) → (lat, lon)."""
    g = grid.strip().upper()
    if len(g) < 4 or not (g[0:2].isalpha() and g[2:4].isdigit()):
        return None
    lon = (ord(g[0]) - ord("A")) * 20 - 180
    lat = (ord(g[1]) - ord("A")) * 10 - 90
    lon += int(g[2]) * 2
    lat += int(g[3]) * 1
    if len(g) >= 6 and g[4].isalpha() and g[5].isalpha():
        lon += (ord(g[4]) - ord("A")) * (2 / 24) + (1 / 24)
        lat += (ord(g[5]) - ord("A")) * (1 / 24) + (1 / 48)
    else:
        lon += 1  # střed 2°×1° pole
        lat += 0.5
    return (lat, lon)


class OkresGeo:
    """Point-in-polygon nad hranicemi okresů z GeoJSON (bez externích závislostí)."""

    # Kandidátní klíče vlastnosti s názvem okresu v různých datasetech.
    NAME_KEYS = ("NAZ_LAU1", "NAZ_OKRES", "NAZEV", "name", "okres", "NAME_2", "NAZEV_LAU")

    def __init__(self, features: list[tuple[str, list]]):
        self._features = features  # [(nazev, [ring, ...]), ...]; ring = [(lon,lat), ...]

    @classmethod
    def load(cls, path: Path) -> "OkresGeo | None":
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        features: list[tuple[str, list]] = []
        for feat in data.get("features", []):
            props = feat.get("properties", {}) or {}
            # Název okresu bývá buď v properties, nebo (jako u siwekm/czech-geojson)
            # přímo na úrovni feature.
            name = next((props[k] for k in cls.NAME_KEYS if props.get(k)), None)
            if not name:
                name = feat.get("name") or next(
                    (feat[k] for k in cls.NAME_KEYS if feat.get(k)), None
                )
            if not name:
                continue
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            coords = geom.get("coordinates") or []
            polygons = []
            if gtype == "Polygon":
                polygons = [coords]
            elif gtype == "MultiPolygon":
                polygons = coords
            else:
                continue
            rings = []
            for poly in polygons:
                for ring in poly:  # [0] vnější, další = díry (pro POC bereme vše)
                    rings.append([(pt[0], pt[1]) for pt in ring])
            if rings:
                features.append((str(name), rings))
        if not features:
            return None
        return cls(features)

    @staticmethod
    def _point_in_ring(lon: float, lat: float, ring: list) -> bool:
        inside = False
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if ((yi > lat) != (yj > lat)) and (
                lon < (xj - xi) * (lat - yi) / (yj - yi) + xi
            ):
                inside = not inside
            j = i
        return inside

    def okres_for(self, lat: float, lon: float) -> str | None:
        for name, rings in self._features:
            if any(self._point_in_ring(lon, lat, ring) for ring in rings):
                return name
        return None


def _load_lookup_csv(path: Path, key_col_hints, val_col_hints, key_norm) -> dict:
    """Načte CSV do dictu {norm(key): value}. Sloupce se hledají podle názvů.

    Ambiguitní klíče (různé hodnoty) se zahodí a jen zalogují – pro POC stačí,
    že je nepočítáme jako úspěch.
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    delimiter = ";" if text.splitlines()[0].count(";") > text.splitlines()[0].count(",") else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    fields = {f.lower(): f for f in (reader.fieldnames or [])}

    def _find(hints):
        for h in hints:
            for low, orig in fields.items():
                if h in low:
                    return orig
        return None

    key_col = _find(key_col_hints)
    val_col = _find(val_col_hints)
    if not key_col or not val_col:
        log.warning("V %s se nepodařilo najít sloupce (klíč=%s, hodnota=%s)",
                    path.name, key_col, val_col)
        return {}

    result: dict = {}
    ambiguous: set = set()
    for row in reader:
        raw_key = (row.get(key_col) or "").strip()
        val = (row.get(val_col) or "").strip()
        if not raw_key or not val:
            continue
        k = key_norm(raw_key)
        if not k:
            continue
        if k in result and result[k] != val:
            ambiguous.add(k)
            continue
        result.setdefault(k, val)
    for k in ambiguous:
        result.pop(k, None)
    if ambiguous:
        log.info("%s: %d nejednoznačných klíčů zahozeno", path.name, len(ambiguous))
    return result


# --- Detekce placeholder / hrubých souřadnic --------------------------------
# Callbooky vrací u stanic bez přesné polohy default (placeholder) souřadnici,
# která by uměle nafoukla jeden okres. Řešíme třemi pravidly:
#   1) kurátorský seznam potvrzených country-defaultů (níže) – zahodit vždy,
#   2) frekvence: souřadnice sdílená ≥ prahem různých značek = placeholder,
#   3) hrubý lokátor (<6 znaků) přesahuje víc okresů → okres z něj neurčovat.
# (lat, lon, tolerance)
CURATED_DEFAULT_COORDS = [
    (50.3125, 14.541667, 0.002),   # QRZ – default OK (grid JO70GH), padá do Mělníka
    (50.07, 14.42, 0.0006),        # HamQTH – default ~střed Prahy
]
DEFAULT_FREQ_THRESHOLD = 8         # sdíleno ≥ tolika značkami → placeholder
MIN_GRID_LEN = 6                   # okres jen z lokátoru délky ≥ 6


def _is_curated_default(lat: float, lon: float) -> bool:
    return any(abs(lat - a) <= t and abs(lon - b) <= t
               for a, b, t in CURATED_DEFAULT_COORDS)


def _coord_key(lat: float, lon: float) -> tuple:
    return (round(lat, 4), round(lon, 4))


def candidate_coord(rec: dict) -> tuple | None:
    """Vrátí (lat, lon, metoda) použitelné pro okres, nebo None.
    Zahodí kurátorské country-defaulty a hrubé (<6 znaků) lokátory.
    Frekvenční placeholdery se řeší až dávkově (viz resolve_batch)."""
    lat, lon = rec.get("lat"), rec.get("lon")
    if lat is not None and lon is not None and not _is_curated_default(lat, lon):
        return (lat, lon, "latlon")
    grid = (rec.get("grid") or "").strip()
    if len(grid) >= MIN_GRID_LEN:
        ll = grid_to_latlon(grid)
        if ll and not _is_curated_default(*ll):
            return (ll[0], ll[1], "grid")
    return None


class OkresResolver:
    """Zkusí odvodit okres všemi dostupnými metodami; vrací (okres, metoda)."""

    def __init__(self):
        self.geo = OkresGeo.load(OKRESY_GEOJSON)
        self.psc = _load_lookup_csv(
            PSC_OKRES_CSV, ("psc", "psč", "zip", "postal"), ("okres",), _norm_psc
        )
        self.obce = _load_lookup_csv(
            OBCE_OKRESY_CSV, ("obec", "obce", "mesto", "město", "nazev", "name"),
            ("okres",), _norm_name,
        )

    @property
    def available(self) -> dict:
        return {
            "latlon": self.geo is not None,
            "grid": self.geo is not None,
            "zip": bool(self.psc),
            "obec": bool(self.obce),
        }

    def resolve(self, rec: dict) -> tuple[str | None, str | None, dict]:
        """Vrátí (okres, metoda, per_metoda_výsledky).

        per_metoda_výsledky: {metoda: okres|None} pro metody, které šlo zkusit.
        Preferované pořadí finálního výsledku: latlon > zip > obec > grid.
        """
        results: dict = {}
        if self.geo is not None:
            lat, lon = rec.get("lat"), rec.get("lon")
            if lat is not None and lon is not None:
                results["latlon"] = self.geo.okres_for(lat, lon)
            grid = rec.get("grid")
            if grid:
                ll = grid_to_latlon(grid)
                results["grid"] = self.geo.okres_for(*ll) if ll else None
        if self.psc:
            z = _norm_psc(rec.get("zip") or "")
            if z:
                results["zip"] = self.psc.get(z)
        if self.obce:
            city = rec.get("addr2") or ""
            if city:
                results["obec"] = self.obce.get(_norm_name(city))
        # okresní znak (kód/název z pole district; kanonický, ale řídce vyplněný) –
        # jako fallback, souřadnice mají přednost kvůli konzistenci názvů okresů
        dcode = rec.get("district_code")
        if dcode:
            results["znak"] = okresy.okres_from_district(dcode)

        for method in ("latlon", "zip", "obec", "grid", "znak"):
            if results.get(method):
                return results[method], method, results
        return None, None, results


# --- Vzorkování + běh --------------------------------------------------------
def sample_active_callsigns(conn: sqlite3.Connection, limit: int) -> list[str]:
    latest = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM daily_stats"
    ).fetchone()["d"]
    if not latest:
        return []
    rows = conn.execute(
        """
        SELECT callsign FROM callsigns
        WHERE last_seen = ?
          AND callsign NOT IN (SELECT callsign FROM callsign_okres)
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (latest, limit),
    ).fetchall()
    return [r["callsign"] for r in rows]


def migrate_purge_personal(conn: sqlite3.Connection) -> None:
    """Jednorázově smaže starou tabulku qrz_lookups s osobními údaji a přenese
    z ní pouze dvojici značka→okres do callsign_okres. Nakonec VACUUM, aby se
    osobní data fyzicky odstranila i z uvolněných stránek souboru DB."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "qrz_lookups" not in tables:
        return
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO callsign_okres
                (callsign, okres, source, method, found, exhausted, fetched_at)
            SELECT callsign, okres,
                   CASE WHEN source IN ('qrz','hamqth','qrzcq') THEN source END,
                   okres_method,
                   found,
                   CASE WHEN okres IS NOT NULL
                             OR source IN ('tried','hamqth_tried') THEN 1 ELSE 0 END,
                   fetched_at
            FROM qrz_lookups
            """
        )
        conn.execute("DROP TABLE qrz_lookups")
    conn.execute("VACUUM")
    log.warning("Osobní data smazána: qrz_lookups zrušena, ponecháno jen "
                "značka→okres (VACUUM proveden).")


def store_okres(conn: sqlite3.Connection, callsign: str, okres: str | None,
                method: str | None, source: str | None, found: bool,
                exhausted: bool) -> None:
    """Uloží VÝHRADNĚ značku→okres + nenosobní metadata. Žádná osobní pole."""
    conn.execute(
        """
        INSERT INTO callsign_okres
            (callsign, okres, source, method, found, exhausted, fetched_at)
        VALUES (:callsign, :okres, :source, :method, :found, :exhausted, :fetched_at)
        ON CONFLICT(callsign) DO UPDATE SET
            okres=excluded.okres, source=excluded.source, method=excluded.method,
            found=excluded.found, exhausted=excluded.exhausted,
            fetched_at=excluded.fetched_at
        """,
        {
            "callsign": callsign,
            "okres": okres,
            "source": source,
            "method": method,
            "found": 1 if found else 0,
            "exhausted": 1 if exhausted else 0,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )


def build_report(conn: sqlite3.Connection, resolver: OkresResolver,
                 total_active: int) -> dict:
    rows = conn.execute("SELECT * FROM callsign_okres").fetchall()
    n = len(rows)
    nf = sum(1 for r in rows if r["found"])

    def pct(x: int, base: int) -> float:
        return round(100 * x / base, 1) if base else 0.0

    with_okres = [r for r in rows if r["okres"]]
    by_method: dict = {}
    by_source: dict = {}
    for r in with_okres:
        by_method[r["method"] or "?"] = by_method.get(r["method"] or "?", 0) + 1
        by_source[r["source"] or "?"] = by_source.get(r["source"] or "?", 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample_size": n,
        "total_active_callsigns": total_active,
        "methods_available": resolver.available,
        "profile_found_any_source": {"count": nf, "pct_of_sample": pct(nf, n)},
        "okres_resolved": {
            "count": len(with_okres),
            "pct_of_sample": pct(len(with_okres), n),
            "pct_of_found": pct(len(with_okres), nf),
            "by_method": by_method,
            "by_source": by_source,
        },
        "extrapolation_full_dataset": {
            "note": "hrubý odhad = míra dohledání na vzorku × počet aktivních značek",
            "estimated_with_okres": round(
                (len(with_okres) / n) * total_active) if n else 0,
        },
    }


def print_report(report: dict) -> None:
    r = report
    print("\n" + "=" * 60)
    print("POC – dohledání okresu ke značkám (QRZ + HamQTH + QRZCQ)")
    print("=" * 60)
    print(f"Vzorek:                  {r['sample_size']}")
    print(f"Aktivních značek celkem: {r['total_active_callsigns']}")
    print(f"Dostupné metody:         {r['methods_available']}")
    f = r["profile_found_any_source"]
    print(f"\nProfil (kterýkoli zdroj):  {f['count']} ({f['pct_of_sample']} % vzorku)")
    o = r["okres_resolved"]
    print("\nOdvozený okres:")
    print(f"  celkem:       {o['count']}  "
          f"({o['pct_of_sample']} % vzorku, {o['pct_of_found']} % nalezených)")
    print(f"  podle zdroje: {o['by_source']}")
    print(f"  podle metody: {o['by_method']}")
    e = r["extrapolation_full_dataset"]
    print(f"\nHrubý odhad pro celý dataset: ~{e['estimated_with_okres']} značek s okresem")
    print("=" * 60 + "\n")


def _safe_lookup(cli, call: str, retries: int = 2, backoff: float = 1.5) -> dict:
    """Dotaz na callbook odolný vůči chybám: nevalidní data → nenalezeno;
    přechodné síťové chyby → pár pokusů, pak přeskočit."""
    for attempt in range(retries + 1):
        try:
            return cli.lookup(call)
        except QRZError as exc:
            log.error("Chyba dat u %s: %s", call, exc)
            return {"found": False}
        except httpx.HTTPError as exc:
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            log.warning("Síťová chyba u %s: %s – přeskakuji", call, exc)
            return {"found": False}
    return {"found": False}


def _lookup_location(call: str, sources: list, sleep: float):
    """Projde zdroje a vrátí (found, coord, cmethod, csource, hint, hsource).
    coord = první použitelná (ne-kurátorská, ne-hrubá) souřadnice; hint =
    zip/město/okresní znak z prvního zdroje, co je má (fallback bez souřadnic).
    Zpracovává se jen v paměti – osobní data se nikam neukládají."""
    found = False
    coord = cmethod = csource = hint = hsource = None
    for name, cli in sources:
        r = _safe_lookup(cli, call)
        time.sleep(sleep)
        if not r.get("found"):
            continue
        found = True
        cc = candidate_coord(r)
        if cc and coord is None:
            coord, cmethod, csource = (cc[0], cc[1]), cc[2], name
            break  # máme použitelnou souřadnici, dál netřeba
        if hint is None and (r.get("zip") or r.get("addr2") or r.get("district_code")):
            hint = {"zip": r.get("zip"), "addr2": r.get("addr2"),
                    "district_code": r.get("district_code")}
            hsource = name
    return found, coord, cmethod, csource, hint, hsource


def resolve_batch(conn: sqlite3.Connection, work: list, sources: list,
                  resolver: "OkresResolver", sleep: float) -> None:
    """Dvoufázově vyřeší okres pro seznam značek a uloží VÝHRADNĚ okres.

    1) sběr kandidátních souřadnic (jen v paměti),
    2) detekce frekvenčních placeholderů (souřadnice sdílená ≥ prahem),
    3) oprava postižených značek jiným zdrojem,
    4) přiřazení okresu a uložení.

    Volá se po dávkách (viz --chunk), takže se ukládá průběžně a přerušení
    ztratí max. jednu dávku. Frekvenční detekce placeholderů (fáze 2) proto
    pracuje v rámci dávky; kurátorský seznam country-defaultů platí globálně.
    Vrací počet značek s přiřazeným okresem.
    """
    geo = resolver.geo
    # FÁZE 1 – sběr
    recs: dict = {}
    coord_calls: dict = defaultdict(set)
    for i, call in enumerate(work, 1):
        found, coord, cmethod, csource, hint, hsource = _lookup_location(
            call, sources, sleep)
        recs[call] = {"found": found, "coord": coord, "cmethod": cmethod,
                      "csource": csource, "hint": hint, "hsource": hsource}
        if coord is not None:
            coord_calls[_coord_key(*coord)].add(call)
        if i % 50 == 0 or i == len(work):
            log.info("… sběr %d/%d", i, len(work))

    # FÁZE 2 – frekvenční placeholdery
    freq_defaults = {ck for ck, cs in coord_calls.items()
                     if len(cs) >= DEFAULT_FREQ_THRESHOLD}
    if freq_defaults:
        log.warning("Placeholder souřadnice (sdílené ≥%d značkami) – zahazuji:",
                    DEFAULT_FREQ_THRESHOLD)
        for ck in sorted(freq_defaults, key=lambda k: -len(coord_calls[k])):
            log.warning("   %s : %d značek", ck, len(coord_calls[ck]))

    # FÁZE 3 – oprava: zkusit u postižených jiný zdroj s ne-placeholder souřadnicí
    repair = [c for c, d in recs.items()
              if d["coord"] and _coord_key(*d["coord"]) in freq_defaults]
    if repair:
        log.info("Oprava %d značek s placeholder souřadnicí (jiný zdroj)…", len(repair))
        for call in repair:
            newcoord = newmethod = newsource = None
            for name, cli in sources:
                r = _safe_lookup(cli, call)
                time.sleep(sleep)
                if not r.get("found"):
                    continue
                cc = candidate_coord(r)
                if cc and _coord_key(cc[0], cc[1]) not in freq_defaults:
                    newcoord, newmethod, newsource = (cc[0], cc[1]), cc[2], name
                    break
            recs[call].update(coord=newcoord, cmethod=newmethod, csource=newsource)

    # FÁZE 4 – přiřazení okresu + uložení (JEN okres)
    resolved = 0
    for call, d in recs.items():
        okres = method = source = None
        coord = d["coord"]
        if coord and _coord_key(*coord) not in freq_defaults and geo:
            okres = geo.okres_for(*coord)
            if okres:
                method, source = d["cmethod"], d["csource"]
        if not okres and d["hint"]:
            ok, m, _ = resolver.resolve(d["hint"])
            if ok:
                okres, method, source = ok, m, d["hsource"]
        if okres:
            resolved += 1
        with conn:
            store_okres(conn, call, okres, method, source, d["found"], exhausted=True)
    log.info("Vyřešeno s okresem: %d z %d", resolved, len(work))
    return resolved


def run_sweep(conn: sqlite3.Connection, *, slice_size: int, chunk: int,
              sleep: float, use_hamqth: bool = True, use_qrzcq: bool = True,
              retry_failed: bool = False, qrz_count_stop: int | None = None,
              resolver: "OkresResolver | None" = None) -> dict:
    """Jeden průběh dohledávání okresu – sdílený pro CLI (main) i denní job.

    Naseeduje nové aktivní značky do fronty (bez lookupu, `fetched_at = datum
    výskytu` → jdou na konec round-robin fronty), vybere nejdéle nezkoušenou
    dávku (`okres IS NULL` seřazeno dle `fetched_at`) a po dávkách ji vyřeší.
    `store_okres()` posune `fetched_at=now`, takže zpracované značky spadnou na
    konec fronty a cyklus se točí. Vrací statistiku běhu.

    `qrz_count_stop`: při dosažení 24h QRZ Countu se běh gracefully zastaví mezi
    dávkami (nezpracované značky zůstanou nedotčené a doberou se příště).
    """
    latest = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM daily_stats"
    ).fetchone()["d"]
    total_active = conn.execute(
        "SELECT COUNT(*) AS n FROM callsigns WHERE last_seen = ?", (latest,)
    ).fetchone()["n"] if latest else 0

    if resolver is None:
        resolver = OkresResolver()
    stats = {"total_active": total_active, "processed": 0, "resolved": 0,
             "seeded": 0, "stopped_by_limit": False}
    if not latest:
        log.warning("Žádný snapshot v daily_stats – není co dohledávat.")
        return stats

    # CLI --retry-failed: znovu otevři dřív vyčerpané (automatický běh je bere i
    # bez toho, protože fronta jede jen dle okres IS NULL + fetched_at).
    if retry_failed:
        with conn:
            conn.execute("UPDATE callsign_okres SET exhausted=0 WHERE okres IS NULL")

    # Seed nových aktivních značek do fronty (bez lookupu). fetched_at = datum
    # výskytu → jdou na konec fronty (odklad prvního pokusu, než si operátor
    # udělá profil na callbooku).
    with conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO callsign_okres
                (callsign, okres, source, method, found, exhausted, fetched_at)
            SELECT c.callsign, NULL, NULL, NULL, 0, 0,
                   c.first_seen || 'T00:00:00+00:00'
            FROM callsigns c
            WHERE c.last_seen = ?
            """,
            (latest,),
        )
        stats["seeded"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    qrz = QRZClient(config.QRZ_USERNAME, config.QRZ_PASSWORD,
                    config.QRZ_AGENT, config.QRZ_XML_URL)
    hamqth = HamQTHClient(config.HAMQTH_USERNAME, config.HAMQTH_PASSWORD,
                          config.HAMQTH_XML_URL)
    qrzcq = QRZCQClient(config.QRZCQ_USERNAME, config.QRZCQ_PASSWORD,
                        config.QRZCQ_XML_URL)
    primary: list = [("qrz", qrz)]
    fallbacks: list = []
    if use_hamqth and hamqth.enabled:
        fallbacks.append(("hamqth", hamqth))
    if use_qrzcq and qrzcq.enabled:
        fallbacks.append(("qrzcq", qrzcq))
    clients = [c for _, c in primary + fallbacks]
    try:
        # QRZ (primární) musí přihlásit; fallbacky při chybě jen přeskočíme.
        for _, client in primary:
            client.login()
        ok_fallbacks: list = []
        for name, client in fallbacks:
            try:
                client.login()
                ok_fallbacks.append((name, client))
            except QRZError as exc:
                log.warning("Zdroj %s vynechán (login selhal): %s", name, exc)
        all_sources = primary + ok_fallbacks
        log.info("Aktivní zdroje: %s", [n for n, _ in all_sources])

        # Round-robin fronta: nejdéle nezkoušené nevyřešené aktivní značky.
        work = [r["callsign"] for r in conn.execute(
            """
            SELECT o.callsign FROM callsign_okres o
            JOIN callsigns c ON c.callsign = o.callsign
            WHERE o.okres IS NULL AND c.last_seen = ?
            ORDER BY o.fetched_at ASC
            LIMIT ?
            """,
            (latest, max(1, slice_size)),
        ).fetchall()]
        log.info("Ke zpracování: %d nevyřešených (nejdéle nezkoušené první; "
                 "naseedováno %d nových; aktivních celkem %d)",
                 len(work), stats["seeded"], total_active)

        chunk = max(1, chunk)
        for start in range(0, len(work), chunk):
            if qrz_count_stop is not None and qrz.lookup_count >= qrz_count_stop:
                log.warning("QRZ 24h Count %d ≥ strop %d – končím, zbytek příště.",
                            qrz.lookup_count, qrz_count_stop)
                stats["stopped_by_limit"] = True
                break
            part = work[start:start + chunk]
            log.info("=== dávka %d–%d z %d ===",
                     start + 1, start + len(part), len(work))
            stats["resolved"] += resolve_batch(
                conn, part, all_sources, resolver, sleep)
            stats["processed"] += len(part)
        log.info("Celkem vyřešeno s okresem: %d z %d zpracovaných",
                 stats["resolved"], stats["processed"])
    finally:
        for c in clients:
            c.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="POC dohledání okresu (QRZ + HamQTH)")
    parser.add_argument("--limit", type=int, default=300, help="velikost vzorku (default 300)")
    parser.add_argument("--sleep", type=float, default=0.3,
                        help="pauza mezi dotazy v sekundách (default 0.3)")
    parser.add_argument("--chunk", type=int, default=100,
                        help="ukládat průběžně po N značkách (default 100); "
                             "menší = odolnější vůči přerušení, slabší detekce "
                             "placeholder souřadnic v rámci dávky")
    parser.add_argument("--report-only", action="store_true",
                        help="jen přepočítat report z cache, bez dotazů")
    parser.add_argument("--no-hamqth", action="store_true", help="nepoužívat HamQTH")
    parser.add_argument("--no-qrzcq", action="store_true", help="nepoužívat QRZCQ")
    parser.add_argument("--retry-failed", action="store_true",
                        help="znovu zkusit značky, u nichž fallbacky dřív selhaly "
                             "(např. po přidání nového zdroje)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    conn = db.connect()
    migrate_purge_personal(conn)
    try:
        resolver = OkresResolver()
        log.info("Dostupné metody odvození okresu: %s", resolver.available)
        if not any(resolver.available.values()):
            log.warning("Žádný referenční geodataset v %s – okres nepůjde odvodit. "
                        "Stáhni data (viz docs/QRZ_POC.md).", GEO_DIR)

        if not args.report_only:
            run_sweep(
                conn,
                slice_size=args.limit,
                chunk=args.chunk,
                sleep=args.sleep,
                use_hamqth=not args.no_hamqth,
                use_qrzcq=not args.no_qrzcq,
                retry_failed=args.retry_failed,
                resolver=resolver,
            )

        latest = conn.execute(
            "SELECT MAX(snapshot_date) AS d FROM daily_stats"
        ).fetchone()["d"]
        total_active = conn.execute(
            "SELECT COUNT(*) AS n FROM callsigns WHERE last_seen = ?", (latest,)
        ).fetchone()["n"] if latest else 0

        report = build_report(conn, resolver, total_active)
        REPORT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print_report(report)
        log.info("Report uložen do %s", REPORT_PATH)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
