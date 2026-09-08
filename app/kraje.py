"""Mapování okres → kraj + kanonické územní kódy.

Doplňuje k odvozenému okresu (viz `app/okresy.py`, `scripts/qrz_poc.py`) kraj
a kódy potřebné pro napojení na polygony mapy:

- **okres**  → LAU kód (`CZ0xxx`, dřív NUTS4)
- **kraj**   → název + NUTS3 kód (`CZ0xx`) + ISO 3166-2 (`CZ-xx`)

Autorova mapa vychází z geoBoundaries, které kraje klíčují přes ISO 3166-2;
NUTS3 je pro křížovou kontrolu. Dělení odpovídá 76 okresům + Praha (stav dle
číselníku ČSÚ, LAU1/okres a NUTS3/kraj).

Klíčuje se přes normalizovaný název okresu (`app.okresy._norm`), takže mapování
sedí na názvy, které vrací point-in-polygon z `okresy.geojson` bez ohledu na
diakritiku/velikost. Názvové varianty geojsonu se řeší v `_ALIASES`.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.okresy import _norm


@dataclass(frozen=True)
class Kraj:
    nazev: str   # úřední název kraje
    nuts: str    # NUTS3, např. CZ064
    iso: str     # ISO 3166-2, např. CZ-64


@dataclass(frozen=True)
class OkresInfo:
    okres: str   # kanonický název okresu
    lau: str     # LAU kód okresu, např. CZ0642
    kraj: Kraj


# --- 14 krajů (NUTS3 + ISO 3166-2) ------------------------------------------
PRAHA = Kraj("Hlavní město Praha", "CZ010", "CZ-10")
STC = Kraj("Středočeský kraj", "CZ020", "CZ-20")
JHC = Kraj("Jihočeský kraj", "CZ031", "CZ-31")
PLK = Kraj("Plzeňský kraj", "CZ032", "CZ-32")
KVK = Kraj("Karlovarský kraj", "CZ041", "CZ-41")
ULK = Kraj("Ústecký kraj", "CZ042", "CZ-42")
LBK = Kraj("Liberecký kraj", "CZ051", "CZ-51")
HKK = Kraj("Královéhradecký kraj", "CZ052", "CZ-52")
PAK = Kraj("Pardubický kraj", "CZ053", "CZ-53")
VYS = Kraj("Kraj Vysočina", "CZ063", "CZ-63")
JHM = Kraj("Jihomoravský kraj", "CZ064", "CZ-64")
OLK = Kraj("Olomoucký kraj", "CZ071", "CZ-71")
ZLK = Kraj("Zlínský kraj", "CZ072", "CZ-72")
MSK = Kraj("Moravskoslezský kraj", "CZ080", "CZ-80")


# --- 77 okresů (LAU kód + kraj) ---------------------------------------------
_OKRESY: list[OkresInfo] = [
    # Praha
    OkresInfo("Hlavní město Praha", "CZ0100", PRAHA),
    # Středočeský
    OkresInfo("Benešov", "CZ0201", STC),
    OkresInfo("Beroun", "CZ0202", STC),
    OkresInfo("Kladno", "CZ0203", STC),
    OkresInfo("Kolín", "CZ0204", STC),
    OkresInfo("Kutná Hora", "CZ0205", STC),
    OkresInfo("Mělník", "CZ0206", STC),
    OkresInfo("Mladá Boleslav", "CZ0207", STC),
    OkresInfo("Nymburk", "CZ0208", STC),
    OkresInfo("Praha-východ", "CZ0209", STC),
    OkresInfo("Praha-západ", "CZ020A", STC),
    OkresInfo("Příbram", "CZ020B", STC),
    OkresInfo("Rakovník", "CZ020C", STC),
    # Jihočeský
    OkresInfo("České Budějovice", "CZ0311", JHC),
    OkresInfo("Český Krumlov", "CZ0312", JHC),
    OkresInfo("Jindřichův Hradec", "CZ0313", JHC),
    OkresInfo("Písek", "CZ0314", JHC),
    OkresInfo("Prachatice", "CZ0315", JHC),
    OkresInfo("Strakonice", "CZ0316", JHC),
    OkresInfo("Tábor", "CZ0317", JHC),
    # Plzeňský
    OkresInfo("Domažlice", "CZ0321", PLK),
    OkresInfo("Klatovy", "CZ0322", PLK),
    OkresInfo("Plzeň-město", "CZ0323", PLK),
    OkresInfo("Plzeň-jih", "CZ0324", PLK),
    OkresInfo("Plzeň-sever", "CZ0325", PLK),
    OkresInfo("Rokycany", "CZ0326", PLK),
    OkresInfo("Tachov", "CZ0327", PLK),
    # Karlovarský
    OkresInfo("Cheb", "CZ0411", KVK),
    OkresInfo("Karlovy Vary", "CZ0412", KVK),
    OkresInfo("Sokolov", "CZ0413", KVK),
    # Ústecký
    OkresInfo("Děčín", "CZ0421", ULK),
    OkresInfo("Chomutov", "CZ0422", ULK),
    OkresInfo("Litoměřice", "CZ0423", ULK),
    OkresInfo("Louny", "CZ0424", ULK),
    OkresInfo("Most", "CZ0425", ULK),
    OkresInfo("Teplice", "CZ0426", ULK),
    OkresInfo("Ústí nad Labem", "CZ0427", ULK),
    # Liberecký
    OkresInfo("Česká Lípa", "CZ0511", LBK),
    OkresInfo("Jablonec nad Nisou", "CZ0512", LBK),
    OkresInfo("Liberec", "CZ0513", LBK),
    OkresInfo("Semily", "CZ0514", LBK),
    # Královéhradecký
    OkresInfo("Hradec Králové", "CZ0521", HKK),
    OkresInfo("Jičín", "CZ0522", HKK),
    OkresInfo("Náchod", "CZ0523", HKK),
    OkresInfo("Rychnov nad Kněžnou", "CZ0524", HKK),
    OkresInfo("Trutnov", "CZ0525", HKK),
    # Pardubický
    OkresInfo("Chrudim", "CZ0531", PAK),
    OkresInfo("Pardubice", "CZ0532", PAK),
    OkresInfo("Svitavy", "CZ0533", PAK),
    OkresInfo("Ústí nad Orlicí", "CZ0534", PAK),
    # Vysočina
    OkresInfo("Havlíčkův Brod", "CZ0631", VYS),
    OkresInfo("Jihlava", "CZ0632", VYS),
    OkresInfo("Pelhřimov", "CZ0633", VYS),
    OkresInfo("Třebíč", "CZ0634", VYS),
    OkresInfo("Žďár nad Sázavou", "CZ0635", VYS),
    # Jihomoravský
    OkresInfo("Blansko", "CZ0641", JHM),
    OkresInfo("Brno-město", "CZ0642", JHM),
    OkresInfo("Brno-venkov", "CZ0643", JHM),
    OkresInfo("Břeclav", "CZ0644", JHM),
    OkresInfo("Hodonín", "CZ0645", JHM),
    OkresInfo("Vyškov", "CZ0646", JHM),
    OkresInfo("Znojmo", "CZ0647", JHM),
    # Olomoucký
    OkresInfo("Jeseník", "CZ0711", OLK),
    OkresInfo("Olomouc", "CZ0712", OLK),
    OkresInfo("Prostějov", "CZ0713", OLK),
    OkresInfo("Přerov", "CZ0714", OLK),
    OkresInfo("Šumperk", "CZ0715", OLK),
    # Zlínský
    OkresInfo("Kroměříž", "CZ0721", ZLK),
    OkresInfo("Uherské Hradiště", "CZ0722", ZLK),
    OkresInfo("Vsetín", "CZ0723", ZLK),
    OkresInfo("Zlín", "CZ0724", ZLK),
    # Moravskoslezský
    OkresInfo("Bruntál", "CZ0801", MSK),
    OkresInfo("Frýdek-Místek", "CZ0802", MSK),
    OkresInfo("Karviná", "CZ0803", MSK),
    OkresInfo("Nový Jičín", "CZ0804", MSK),
    OkresInfo("Opava", "CZ0805", MSK),
    OkresInfo("Ostrava-město", "CZ0806", MSK),
]

# Index podle normalizovaného kanonického názvu.
_INDEX: dict[str, OkresInfo] = {_norm(o.okres): o for o in _OKRESY}

# Názvové varianty, které vrací geojson / callbooky, mimo kanonický název.
# Klíč = alternativní název, hodnota = kanonický název okresu.
_ALIASES: dict[str, str] = {
    "území Hlavního města Prahy": "Hlavní město Praha",
    "Hlavní město Praha": "Hlavní město Praha",
    "Praha": "Hlavní město Praha",
    "Hlavni mesto Praha": "Hlavní město Praha",
}
for _alt, _canon in _ALIASES.items():
    _INDEX.setdefault(_norm(_alt), _INDEX[_norm(_canon)])


def lookup(okres_name: str | None) -> OkresInfo | None:
    """Vrátí `OkresInfo` (kanonický okres + LAU + kraj) pro název okresu.

    Tolerantní k diakritice/velikosti a názvovým variantám (Praha). Vrátí None,
    pokud okres nezná — volající by to měl zalogovat, ne tiše zahodit."""
    if not okres_name:
        return None
    return _INDEX.get(_norm(okres_name))
