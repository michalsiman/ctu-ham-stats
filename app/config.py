"""Konfigurace aplikace. Vše lze přepsat proměnnými prostředí."""
import configparser
import os
from pathlib import Path

# Volitelný config.ini (v kořeni projektu), proměnné prostředí mají přednost.
CONFIG_INI_PATH = Path(os.getenv("CONFIG_INI_PATH", Path(__file__).resolve().parent.parent / "config.ini"))
# Neverzovaný overlay pro citlivé hodnoty (hesla apod.). config.ini je v gitu,
# proto tajemství patří sem (nebo do proměnných prostředí). Hodnoty z local
# přepisují config.ini.
CONFIG_LOCAL_INI_PATH = Path(
    os.getenv("CONFIG_LOCAL_INI_PATH", Path(__file__).resolve().parent.parent / "config.local.ini")
)

_ini = configparser.ConfigParser()
if CONFIG_INI_PATH.is_file():
    _ini.read(CONFIG_INI_PATH, encoding="utf-8")
if CONFIG_LOCAL_INI_PATH.is_file():
    _ini.read(CONFIG_LOCAL_INI_PATH, encoding="utf-8")


def _ini_get(section: str, option: str, fallback: str) -> str:
    return _ini.get(section, option, fallback=fallback)

# URL denního CSV exportu ČTÚ (otevřená data, aktualizace denně ~05:00)
CSV_URL = os.getenv(
    "CTU_CSV_URL",
    "https://data.ctu.gov.cz/sites/default/files/imports/"
    "import_radiove_kmitocty/import_radiove_kmitocty_opravneni.csv",
)

# Statistiky německých personengebundených značek (aktuální stav).
DE_RUFZEICHEN_STATS_URL = os.getenv(
    "DE_RUFZEICHEN_STATS_URL",
    "https://www.12db.de/rufzeichen/statistik/",
)

# World Bank API – počet obyvatel ČR (aktualizováno ročně).
CZ_POPULATION_URL = os.getenv(
    "CZ_POPULATION_URL",
    "https://api.worldbank.org/v2/country/CZ/indicator/SP.POP.TOTL?format=json&mrv=1",
)

# World Bank API – počet obyvatel Německa (aktualizováno ročně).
DE_POPULATION_URL = os.getenv(
    "DE_POPULATION_URL",
    "https://api.worldbank.org/v2/country/DE/indicator/SP.POP.TOTL?format=json&mrv=1",
)

# Adresář s daty (SQLite + archiv CSV)
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
ARCHIVE_DIR = DATA_DIR / "archive"
DB_PATH = DATA_DIR / "hamstats.db"

# Časy denního stažení (lokální čas kontejneru, HH:MM oddělené čárkou).
# Data ČTÚ se v průběhu dne mění, proto stahujeme víckrát.
INGEST_TIMES = os.getenv("INGEST_TIMES", "06:00,14:00")

# Sůl pro anonymizaci identifikace návštěvníka (IP + User-Agent).
VISIT_HASH_SALT = os.getenv("VISIT_HASH_SALT", "ctu-ham-stats")

# Maskování vybrané volací značky (kdekoli v aplikaci) na náhradní text.
# Nastavuje se v config.ini (sekce [callsign_mask]) nebo proměnnými prostředí.
MASK_CALLSIGN_ENABLED = os.getenv(
    "MASK_CALLSIGN_ENABLED", _ini_get("callsign_mask", "enabled", "false")
).strip().lower() in ("1", "true", "yes", "on")

MASK_CALLSIGN_VALUE = os.getenv(
    "MASK_CALLSIGN_VALUE", _ini_get("callsign_mask", "callsign", "OL60UFM")
).strip().upper()

MASK_CALLSIGN_REPLACEMENT = os.getenv(
    "MASK_CALLSIGN_REPLACEMENT", _ini_get("callsign_mask", "replacement", "neznámá")
)

# Povolené hodnoty hlavičky Host pro MCP endpoint (/mcp) – ochrana proti
# DNS-rebindingu. Za reverzní proxy uveď veřejné hostname webu. localhost se
# povolí vždy. Prázdná hodnota = ochranu vypnout (řeší proxy).
MCP_ALLOWED_HOSTS = os.getenv(
    "MCP_ALLOWED_HOSTS", _ini_get("mcp", "allowed_hosts", "")
)


# --- QRZ.com XML API (POC dohledání okresu ke značkám) -----------------------
# Přístup vyžaduje jméno+heslo předplaceného účtu (XML/Logbook Data). Server
# vrátí session key, ten se cachuje a používá pro jednotlivé lookupy.
# POZOR: config.ini je verzovaný – creds dej do config.local.ini nebo do env.
QRZ_USERNAME = os.getenv("QRZ_USERNAME", _ini_get("qrz", "username", ""))
QRZ_PASSWORD = os.getenv("QRZ_PASSWORD", _ini_get("qrz", "password", ""))
QRZ_AGENT = os.getenv("QRZ_AGENT", _ini_get("qrz", "agent", "ctu-ham-stats"))
QRZ_XML_URL = os.getenv(
    "QRZ_XML_URL", _ini_get("qrz", "xml_url", "https://xmldata.qrz.com/xml/current/")
)

# --- HamQTH.com XML API (druhý zdroj, doplňuje pokrytí QRZ) -------------------
# Zdarma po registraci; session id platí 1 hodinu. Login jménem+heslem.
HAMQTH_USERNAME = os.getenv("HAMQTH_USERNAME", _ini_get("hamqth", "username", ""))
HAMQTH_PASSWORD = os.getenv("HAMQTH_PASSWORD", _ini_get("hamqth", "password", ""))
HAMQTH_XML_URL = os.getenv(
    "HAMQTH_XML_URL", _ini_get("hamqth", "xml_url", "https://www.hamqth.com/xml.php")
)

# --- QRZCQ.com XML API (volitelný třetí zdroj) -------------------------------
# Samostatný účet (creds z QRZ ani HamQTH zde neplatí); session key platí 3 dny.
QRZCQ_USERNAME = os.getenv("QRZCQ_USERNAME", _ini_get("qrzcq", "username", ""))
QRZCQ_PASSWORD = os.getenv("QRZCQ_PASSWORD", _ini_get("qrzcq", "password", ""))
QRZCQ_XML_URL = os.getenv(
    "QRZCQ_XML_URL", _ini_get("qrzcq", "xml_url", "https://ssl.qrzcq.com/xml")
)

# --- Denní dohledávání okresu (app.okres) ------------------------------------
# Aplikace 1x denně (dle plánu) projede callbooky a doplní okres značkám, které
# ho nemají (paced round-robin fronta dle fetched_at). Vyžaduje QRZ creds –
# bez nich se job neregistruje. Creds patří do config.local.ini (sekce [qrz]).
OKRES_LOOKUP_ENABLED = os.getenv(
    "OKRES_LOOKUP_ENABLED", _ini_get("okres", "enabled", "true")
).strip().lower() in ("1", "true", "yes", "on")

# Časy spuštění (HH:MM oddělené čárkou) – default brzy ráno, mimo ingest.
OKRES_LOOKUP_TIMES = os.getenv(
    "OKRES_LOOKUP_TIMES", _ini_get("okres", "times", "03:30")
)
# Dny běhu (APScheduler day_of_week): "*" = denně, nebo např. "mon" = jen pondělí.
OKRES_LOOKUP_DAYS = os.getenv(
    "OKRES_LOOKUP_DAYS", _ini_get("okres", "days", "*")
).strip() or "*"

# Kolik nevyřešených značek zpracovat za jeden běh (velikost dávky fronty).
OKRES_SLICE = int(os.getenv("OKRES_SLICE", _ini_get("okres", "slice", "150")))
# Ukládat průběžně po N značkách (odolnost vůči přerušení).
OKRES_CHUNK = int(os.getenv("OKRES_CHUNK", _ini_get("okres", "chunk", "100")))
# Pauza mezi dotazy na callbook (throttling), v sekundách.
OKRES_SLEEP = float(os.getenv("OKRES_SLEEP", _ini_get("okres", "sleep", "0.5")))
# Bezpečný strop 24h QRZ lookupů – při dosažení se běh gracefully zastaví
# (chrání účet před odpojením; nezpracované značky se doberou příště).
OKRES_QRZ_COUNT_STOP = int(
    os.getenv("OKRES_QRZ_COUNT_STOP", _ini_get("okres", "qrz_count_stop", "7000"))
)


def mcp_allowed_hosts() -> list[str]:
    """Naparsuje MCP_ALLOWED_HOSTS na seznam hostname (bez prázdných položek)."""
    return [h.strip() for h in MCP_ALLOWED_HOSTS.split(",") if h.strip()]


def ingest_times() -> list[tuple[int, int]]:
    """Naparsuje INGEST_TIMES na seznam (hodina, minuta)."""
    times: list[tuple[int, int]] = []
    for part in INGEST_TIMES.split(","):
        part = part.strip()
        if not part:
            continue
        hour, _, minute = part.partition(":")
        times.append((int(hour), int(minute or 0)))
    return times or [(6, 0)]


def okres_lookup_times() -> list[tuple[int, int]]:
    """Naparsuje OKRES_LOOKUP_TIMES na seznam (hodina, minuta)."""
    times: list[tuple[int, int]] = []
    for part in OKRES_LOOKUP_TIMES.split(","):
        part = part.strip()
        if not part:
            continue
        hour, _, minute = part.partition(":")
        times.append((int(hour), int(minute or 0)))
    return times or [(3, 30)]
