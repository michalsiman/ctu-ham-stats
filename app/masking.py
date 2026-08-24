"""Maskování konfigurované volací značky ve výstupech aplikace (API i weby).

Chování se řídí `config.MASK_CALLSIGN_*`, které lze nastavit v config.ini
nebo proměnnými prostředí – viz app/config.py.
"""
from typing import Any

from . import config


def mask_data(value: Any) -> Any:
    """Rekurzivně projde datovou strukturu a nahradí přesnou shodu s
    maskovanou značkou (bez ohledu na velikost písmen) náhradním textem.
    """
    if not config.MASK_CALLSIGN_ENABLED or not config.MASK_CALLSIGN_VALUE:
        return value
    if isinstance(value, str):
        if value.strip().upper() == config.MASK_CALLSIGN_VALUE:
            return config.MASK_CALLSIGN_REPLACEMENT
        return value
    if isinstance(value, dict):
        return {k: mask_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_data(v) for v in value]
    if isinstance(value, tuple):
        return tuple(mask_data(v) for v in value)
    return value
