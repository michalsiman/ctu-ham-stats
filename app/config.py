"""Konfigurace aplikace. Vše lze přepsat proměnnými prostředí."""
import configparser
import os
from pathlib import Path

# Volitelný config.ini (v kořeni projektu), proměnné prostředí mají přednost.
CONFIG_INI_PATH = Path(os.getenv("CONFIG_INI_PATH", Path(__file__).resolve().parent.parent / "config.ini"))

_ini = configparser.ConfigParser()
if CONFIG_INI_PATH.is_file():
    _ini.read(CONFIG_INI_PATH, encoding="utf-8")


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
