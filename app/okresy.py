"""Číselník okresních znaků (okresní kódy používané v závodech).

Zdroj: Český radioklub – Tabulka okresních znaků
https://ceskyradioklub.cz/provoz/kv/tabulka-okresnich-znaku
(dělení dle stavu k 31. 12. 2002; 76 okresů + Praha rozdělená na Praha 1–10).

Kód okresu (3 písmena, např. FPA = Pardubice) je kanonický identifikátor okresu,
který operátoři běžně vyplňují. Slouží k odvození okresu z pole `district`
vráceného HamQTH i jako jednotka pro mapu.
"""

# Kód → název okresu (dle ČRK).
OKRESNI_ZNAKY: dict[str, str] = {
    # Praha
    "APA": "Praha 1", "APB": "Praha 2", "APC": "Praha 3", "APD": "Praha 4",
    "APE": "Praha 5", "APF": "Praha 6", "APG": "Praha 7", "APH": "Praha 8",
    "API": "Praha 9", "APJ": "Praha 10", "BPZ": "Praha-západ", "BPV": "Praha-východ",
    # Střední Čechy
    "BBN": "Benešov", "BBE": "Beroun", "BKD": "Kladno", "BKO": "Kolín",
    "BKH": "Kutná Hora", "BME": "Mělník", "BMB": "Mladá Boleslav", "BNY": "Nymburk",
    "BPB": "Příbram", "BRA": "Rakovník",
    # Jižní Čechy
    "CBU": "České Budějovice", "CCK": "Český Krumlov", "CJH": "Jindřichův Hradec",
    "CPE": "Pelhřimov", "CPI": "Písek", "CPR": "Prachatice", "CST": "Strakonice",
    "CTA": "Tábor",
    # Západní Čechy
    "DDO": "Domažlice", "DCH": "Cheb", "DKV": "Karlovy Vary", "DKL": "Klatovy",
    "DPM": "Plzeň-město", "DPJ": "Plzeň-jih", "DPS": "Plzeň-sever", "DRO": "Rokycany",
    "DSO": "Sokolov", "DTA": "Tachov",
    # Severní Čechy
    "ECL": "Česká Lípa", "EDE": "Děčín", "ECH": "Chomutov", "EJA": "Jablonec nad Nisou",
    "ELI": "Liberec", "ELT": "Litoměřice", "ELO": "Louny", "EMO": "Most",
    "ETE": "Teplice", "EUL": "Ústí nad Labem",
    # Východní Čechy
    "FHB": "Havlíčkův Brod", "FHK": "Hradec Králové", "FCR": "Chrudim", "FJI": "Jičín",
    "FNA": "Náchod", "FPA": "Pardubice", "FRK": "Rychnov nad Kněžnou", "FSE": "Semily",
    "FSV": "Svitavy", "FTR": "Trutnov", "FUO": "Ústí nad Orlicí",
    # Morava
    "GBL": "Blansko", "GBM": "Brno-město", "GBV": "Brno-venkov", "GBR": "Břeclav",
    "GHO": "Hodonín", "GJI": "Jihlava", "GKR": "Kroměříž", "GPR": "Prostějov",
    "GTR": "Třebíč", "GUH": "Uherské Hradiště", "GVY": "Vyškov", "GZL": "Zlín",
    "GZN": "Znojmo", "GZS": "Žďár nad Sázavou",
    # Slezsko
    "HBR": "Bruntál", "HFM": "Frýdek-Místek", "HJE": "Jeseník", "HKA": "Karviná",
    "HNJ": "Nový Jičín", "HOL": "Olomouc", "HOP": "Opava", "HOS": "Ostrava-město",
    "HPR": "Přerov", "HSU": "Šumperk", "HVS": "Vsetín",
}

# Praha 1–10 spadá pod jeden polygon "Hlavní město Praha" na okresní mapě.
_PRAHA_CODES = {"APA", "APB", "APC", "APD", "APE", "APF", "APG", "APH", "API", "APJ"}


import unicodedata


def _norm(text: str) -> str:
    n = unicodedata.normalize("NFKD", text.strip().upper())
    return "".join(ch for ch in n if ch.isalnum() and not unicodedata.combining(ch))


# Index názvů okresů bez diakritiky/velikosti pro tolerantní hledání.
_NAME_INDEX: dict[str, str] = {_norm(name): name for name in OKRESNI_ZNAKY.values()}


def okres_from_code(code: str | None) -> str | None:
    """Vrátí název okresu pro okresní znak (case-insensitive), jinak None."""
    if not code:
        return None
    return OKRESNI_ZNAKY.get(code.strip().upper())


def okres_from_district(value: str | None) -> str | None:
    """Odvodí okres z pole `district` (HamQTH). Přijme okresní znak (APB) i název
    okresu ve volném textu (case/diakritika se ignoruje). Jinak None."""
    if not value:
        return None
    v = value.strip()
    by_code = OKRESNI_ZNAKY.get(v.upper())
    if by_code:
        return by_code
    return _NAME_INDEX.get(_norm(v))


def okres_map_unit(code: str | None) -> str | None:
    """Jako okres_from_code, ale Praha 1–10 se sloučí na 'Hlavní město Praha'
    (jednotka pro choropleth mapu, kde je Praha jeden okres)."""
    if not code:
        return None
    c = code.strip().upper()
    if c in _PRAHA_CODES:
        return "Hlavní město Praha"
    return OKRESNI_ZNAKY.get(c)
