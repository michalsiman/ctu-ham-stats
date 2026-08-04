"""Konfigurace aplikace. Vše lze přepsat proměnnými prostředí."""
import os
from pathlib import Path

# URL denního CSV exportu ČTÚ (otevřená data, aktualizace denně ~05:00)
CSV_URL = os.getenv(
    "CTU_CSV_URL",
    "https://data.ctu.gov.cz/sites/default/files/imports/"
    "import_radiove_kmitocty/import_radiove_kmitocty_opravneni.csv",
)

# Adresář s daty (SQLite + archiv CSV)
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
ARCHIVE_DIR = DATA_DIR / "archive"
DB_PATH = DATA_DIR / "hamstats.db"

# Časy denního stažení (lokální čas kontejneru, HH:MM oddělené čárkou).
# Data ČTÚ se v průběhu dne mění, proto stahujeme víckrát.
INGEST_TIMES = os.getenv("INGEST_TIMES", "06:00,14:00")


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
