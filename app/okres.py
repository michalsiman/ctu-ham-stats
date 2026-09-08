"""Denní dohledávání okresu ke značkám – orchestrace pro plánovač + endpoint.

Zavolá paced round-robin sweep (`scripts.qrz_poc.run_sweep`) nad aktivními
značkami bez okresu a promítne výsledek do `callsign_region` (`app.region`),
aby se aktuální stav rovnou objevil v mapě/agregacích.

Bez QRZ credentials nebo při `OKRES_LOOKUP_ENABLED=false` je to no-op (jen log),
takže nasazení bez přístupů do callbooků nespadne.
"""
from __future__ import annotations

import logging

from . import config, db, region

log = logging.getLogger(__name__)


def run_okres_lookup() -> dict:
    """Jeden běh: paced sweep + promítnutí do callsign_region.

    Vrací sloučenou statistiku (klíče ze `run_sweep` + `region`).
    """
    if not config.OKRES_LOOKUP_ENABLED:
        log.info("Okres lookup vypnutý (OKRES_LOOKUP_ENABLED=false) – přeskakuji.")
        return {"skipped": "disabled"}
    if not config.QRZ_USERNAME or not config.QRZ_PASSWORD:
        log.warning("Chybí QRZ credentials – okres lookup přeskočen.")
        return {"skipped": "no_credentials"}

    # Lazy import: qrz_poc táhne httpx a geodata; ať start aplikace na tom nevisí.
    from scripts import qrz_poc

    conn = db.connect()
    try:
        sweep = qrz_poc.run_sweep(
            conn,
            slice_size=config.OKRES_SLICE,
            chunk=config.OKRES_CHUNK,
            sleep=config.OKRES_SLEEP,
            qrz_count_stop=config.OKRES_QRZ_COUNT_STOP,
        )
        reg = region.refresh_region(conn)
    finally:
        conn.close()

    log.info("Okres lookup hotov: zpracováno %d, vyřešeno %d, region zapsáno %d%s.",
             sweep.get("processed", 0), sweep.get("resolved", 0),
             reg.get("written", 0),
             " (zastaveno QRZ limitem)" if sweep.get("stopped_by_limit") else "")
    return {**sweep, "region": reg}
