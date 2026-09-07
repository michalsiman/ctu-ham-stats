#!/usr/bin/env python3
"""Sestaví referenční číselníky PSČ→okres a obec→okres pro odvození okresu.

Odemyká v resolveru (`scripts/qrz_poc.py`) metody `zip` a `obec`: když callbook
nevrátí souřadnice, ale má PSČ nebo město, dá se z nich okres dohledat.

Zdroje (otevřená data):
- **obec→okres**: ČSÚ „Struktura území ČR" (`struktura_uzemi_cr.csv`) – seznam
  obcí s příslušností do okresu/kraje (názvy i kódy dle číselníků ČSÚ/RÚIAN).
- **PSČ→okres**: Česká pošta „Seznam PSČ částí obcí a obcí bez částí"
  (`zv_pcobc.csv`, Windows-1250, oddělovač `;`).

Výstup do `data/geo/`:
- `obce_okresy.csv`  se sloupci `obec,okres`
- `psc_okres.csv`    se sloupci `psc,okres`

Názvy okresů se kanonizují přes `app.kraje` (zaručí, že jsou známé a konzistentní
se zbytkem projektu). Nejednoznačné klíče (stejné PSČ / stejný název obce ve víc
okresech) se sem klidně zapíšou vícekrát – resolver je při načtení sám zahodí
(`_load_lookup_csv`), protože z nich okres jednoznačně neurčí.

Použití:
    python scripts/build_geo_ciselnik.py                      # stáhne a sestaví
    python scripts/build_geo_ciselnik.py \\
        --struktura /tmp/struktura.csv --pcobc /tmp/zv_pcobc.csv   # z lokálních souborů
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, kraje  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("build_geo")

GEO_DIR = config.DATA_DIR / "geo"

# Přímé odkazy na otevřená data (stav 2026, aktualizace k 1. dni v měsíci).
STRUKTURA_URL = ("https://csu.gov.cz/docs/107516/"
                 "f1a13e13-9af2-462b-4b9e-2f0aee9997c6/struktura_uzemi_cr.csv")
PCOBC_ZIP_URL = "https://www.ceskaposta.cz/documents/d/guest/db_pcobc-zip?download=true"


def _fetch(url: str) -> bytes:
    log.info("Stahuji %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "ctu-ham-stats/geo"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (důvěryhodné URL)
        return resp.read()


def load_struktura(path: Path | None) -> list[tuple[str, str]]:
    """Vrátí seznam (obec, okres_text) z ČSÚ struktury území."""
    raw = path.read_bytes() if path else _fetch(STRUKTURA_URL)
    text = raw.decode("utf-8-sig")
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        obec, okres = (r.get("obec_text") or "").strip(), (r.get("okres_text") or "").strip()
        if obec and okres:
            rows.append((obec, okres))
    return rows


def load_pcobc(path: Path | None) -> list[tuple[str, str]]:
    """Vrátí seznam (psc, okres_text) ze souboru České pošty."""
    if path:
        text = path.read_bytes().decode("cp1250")
    else:
        data = _fetch(PCOBC_ZIP_URL)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            text = z.read(name).decode("cp1250")
    rows = []
    for r in csv.DictReader(io.StringIO(text), delimiter=";"):
        psc, okres = (r.get("PSC") or "").strip(), (r.get("NAZOKRESU") or "").strip()
        if psc and okres:
            rows.append((psc, okres))
    return rows


def _canon(pairs: list[tuple[str, str]], key_label: str) -> tuple[list[tuple[str, str]], set]:
    """Kanonizuje okres přes kraje.lookup; vrátí (dvojice, nenapojené_okresy)."""
    out, unmapped = [], set()
    for key, okres in pairs:
        info = kraje.lookup(okres)
        if info is None:
            unmapped.add(okres)
            continue
        out.append((key, info.okres))
    return out, unmapped


def _write_csv(path: Path, header: tuple[str, str], rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    log.info("Zapsáno %d řádků → %s", len(rows), path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--struktura", type=Path, help="lokální struktura_uzemi_cr.csv (jinak stáhnout)")
    ap.add_argument("--pcobc", type=Path, help="lokální zv_pcobc.csv (jinak stáhnout ZIP)")
    args = ap.parse_args()

    obce_pairs, obce_unmapped = _canon(load_struktura(args.struktura), "obec")
    psc_pairs, psc_unmapped = _canon(load_pcobc(args.pcobc), "psc")

    for name, unmapped in (("obce", obce_unmapped), ("psc", psc_unmapped)):
        if unmapped:
            log.warning("%s: %d neznámých okresů (přeskočeno): %s",
                        name, len(unmapped), sorted(unmapped))

    _write_csv(GEO_DIR / "obce_okresy.csv", ("obec", "okres"), obce_pairs)
    _write_csv(GEO_DIR / "psc_okres.csv", ("psc", "okres"), psc_pairs)
    log.info("Hotovo. Resolver teď umí metody 'obec' a 'zip'. "
             "Doplnění nenalezených: python scripts/qrz_poc.py --retry-failed --limit 0")


if __name__ == "__main__":
    main()
