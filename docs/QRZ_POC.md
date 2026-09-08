# POC: dohledání okresu ke značkám přes QRZ.com

Cíl POC je **změřit**, jestli má smysl dohledávat okres operátora přes callbooky
(QRZ.com / HamQTH / QRZCQ) a stavět z toho mapu počtu koncesí po okresech. Data
z ČTÚ obsahují jen značku, referenci a platnost — žádnou lokalitu.

## Ochrana soukromí (privacy-by-design)

Jediný údaj, který nás zajímá a který se ukládá, je **okres**. Odpovědi callbooků
(jméno, adresa, PSČ, souřadnice, lokátor) se zpracují **jen v paměti** — spočítá
se z nich okres a vzápětí se zahodí. Do DB (tabulka `callsign_okres`) jde
**výhradně dvojice značka→okres** + nenosobní metadata (zdroj, metoda, čas).
Žádné jméno, adresa, PSČ, souřadnice ani lokátor se neukládají ani nelogují;
klienti taková pole ani nenačítají. Veřejně se publikují pouze **agregované počty
na okres**. (Značky jsou navíc už teď veřejná otevřená data ČTÚ a okres je hrubý —
celý okres, ne adresa.)

## Co QRZ vrací (a co ne)

QRZ.com XML API pro **české (OK/OL) značky nevrací okres přímo** — pole
`state`, `county`, `fips` jsou pouze pro USA. Pro ČR jsou k dispozici nanejvýš:
jméno, ulice (`addr1`), město (`addr2`), PSČ (`zip`), lokátor (`grid`) a
souřadnice (`lat`/`lon`). Navíc jde o crowd-sourced databázi — ne každý OK
operátor tam má záznam a ne každý má vyplněnou adresu. Přesně tuhle úspěšnost
POC měří.

Okres se proto **odvozuje** z toho, co QRZ vrátí. POC zkouší všechny metody:

| Metoda | Vstup z QRZ | Referenční dataset |
|--------|-------------|--------------------|
| `latlon` | `lat`/`lon` | `data/geo/okresy.geojson` (point-in-polygon) |
| `zip`    | `zip` (PSČ) | `data/geo/psc_okres.csv` |
| `obec`   | `addr2` (město) | `data/geo/obce_okresy.csv` |
| `grid`   | `grid` (lokátor) | `data/geo/okresy.geojson` (fallback přes střed lokátoru) |

Chybějící dataset = daná metoda se přeskočí a report ji označí jako nedostupnou.

### Filtrace placeholder / hrubých souřadnic

Callbooky vrací u stanic bez přesné polohy **default (placeholder) souřadnici**
(např. QRZ pro OK `50.3125/14.5417` = grid JO70GH, padá do okresu Mělník;
HamQTH ~`50.07/14.42`). Bez filtrace by uměle nafoukly jeden okres. Řeší se
dvoufázově (`resolve_batch` v `scripts/qrz_poc.py`):

1. **Kurátorský seznam** potvrzených country-defaultů → zahodit vždy.
2. **Frekvence**: souřadnice sdílená ≥ `DEFAULT_FREQ_THRESHOLD` (8) různými
   značkami = placeholder → zahodit. Malé reálné shluky (rodina, obec, stejný
   6-znakový lokátor) zůstanou.
3. **Hrubý lokátor** (< 6 znaků) přesahuje víc okresů → okres z něj neurčovat.
4. **Oprava**: značce s placeholder souřadnicí se zkusí najít reálná poloha
   z jiného zdroje.
5. Zahozené souřadnice se **logují** (žádné tiché mazání).

## Přístup k QRZ

XML API vyžaduje **jméno + heslo předplaceného účtu** (QRZ XML/Logbook Data
subscription), ne prostý API klíč. Po přihlášení server vrátí session key, ten
se cachuje a používá pro jednotlivé lookupy.

`config.ini` je verzovaný v gitu — **hesla do něj nedávej.** Použij buď proměnné
prostředí, nebo neverzovaný `config.local.ini` (je v `.gitignore`):

```ini
# config.local.ini
[qrz]
username = OK1XXX
password = tvoje-heslo
agent = ctu-ham-stats
```

nebo:

```bash
export QRZ_USERNAME=OK1XXX
export QRZ_PASSWORD=...
```

### HamQTH (druhý zdroj)

HamQTH.com je zdarma (po registraci) a u OK operátorů doplňuje pokrytí QRZ. Login
je jméno+heslo (session id platí 1 h). Použije se jako **fallback** pro značky,
které QRZ nedohledá. Vypnout lze přepínačem `--no-hamqth`.

```ini
# config.local.ini
[hamqth]
username = OK1XXX
password = ...
```

### QRZCQ (volitelný třetí zdroj)

QRZCQ.com je zapojený jako další fallback. Vyžaduje **samostatný účet s premium**
(login z QRZ ani HamQTH tam neplatí; bez premia vrací XML API „Premium Required").
S premiem má u OK stanic silné pokrytí a v POC přidal ~12 p.b. Vypnout: `--no-qrzcq`.

```ini
# config.local.ini
[qrzcq]
username = ...
password = ...
```

Po přidání nového zdroje spusť `python scripts/qrz_poc.py --retry-failed`, aby se
znovu zkusily značky, u nichž dřívější fallbacky selhaly.

## Referenční geodata

Stáhni jednorázově do `data/geo/` (adresář je pod `data/`, tedy mimo git):

- **Hranice okresů** — GeoJSON s polygony okresů (ČÚZK / ARČR 500 nebo jiný
  otevřený zdroj) jako `okresy.geojson`. Skript hledá název okresu ve
  vlastnostech pod klíči `NAZ_LAU1`, `NAZ_OKRES`, `NAZEV`, `name`, `okres`, …
  (souřadnice ve formátu GeoJSON `[lon, lat]`, WGS84).
- **PSČ → okres** — CSV `psc_okres.csv` se sloupci obsahujícími PSČ a okres
  (názvy sloupců stačí, když obsahují „psc"/„psč"/„zip" a „okres").
- **Obec → okres** — CSV `obce_okresy.csv` (sloupce s „obec"/„nazev"/„mesto" a
  „okres"). Nejednoznačné názvy obcí se u POC zahazují.

Skript funguje i s částí datasetů — jen zúží počet dostupných metod.

## Spuštění

```bash
# vzorek ~300 náhodných aktivních značek (default)
python scripts/qrz_poc.py

# jiná velikost vzorku / rychlost
python scripts/qrz_poc.py --limit 500 --sleep 0.5

# jen přepočítat report z cache (bez dotazů na QRZ)
python scripts/qrz_poc.py --report-only
```

Odvozené okresy se ukládají do tabulky `callsign_okres` v `data/hamstats.db`
(jen značka→okres + metadata, viz *Ochrana soukromí*). Značky už jednou
zpracované se **přeskočí** (nedotazují se znovu). Report se
uloží do `data/qrz_poc_report.json` a vypíše na stdout.

## Jak číst report

- **Záznam na QRZ** — kolik značek ze vzorku má na QRZ vůbec profil.
- **Vyplněnost polí** — z nalezených profilů, kolik má město / PSČ / lokátor /
  souřadnice. Ukáže, která metoda odvození má šanci na největší pokrytí.
- **Odvozený okres** — kolika značkám se okres přiřadil aspoň jednou metodou,
  s rozpadem podle metody.
- **Odhad pro celý dataset** — extrapolace míry dohledání na ~5100 aktivních
  značek.

## Výsledek POC (2026-09-01)

Vzorek 600 náhodných aktivních značek. Dva zdroje: **QRZ.com** a jako fallback
**HamQTH.com**. Okres se odvozuje přes lat/lon a lokátor → point-in-polygon
(`data/geo/okresy.geojson`, 77 okresů; zdroj
[siwekm/czech-geojson](https://github.com/siwekm/czech-geojson)).

Tři zdroje: **QRZ.com** + fallback **HamQTH.com** + fallback **QRZCQ.com**
(QRZCQ vyžaduje premium).

**Celý vzorek (vč. klubů, majáků/převaděčů OK0, speciálů), n=600:**

| Metrika | QRZ | + HamQTH | + QRZCQ |
|---|---|---|---|
| Profil nalezen | 54 % | 68,5 % | **87,7 %** |
| **Okres odvozen** | 53 % | 65,8 % | **79,5 %** |
| Odhad pro celý dataset (~5114) | ~2710 | ~3367 | **~4066** |

**Jen běžní operátoři (bez klubů/OK0/speciálů), n=544:**

| Metrika | QRZ | + HamQTH | + QRZCQ |
|---|---|---|---|
| Profil nalezen | 56 % | 72 % | **90 %** |
| **Okres odvozen** | 56 % | 69 % | **81 %** |
| Odhad běžných operátorů (~4692) | ~2630 | ~3217 | **~3804** |

Zdroj vyřešených okresů (běžní operátoři): QRZ 293, HamQTH 80, QRZCQ 68.

> **Oprava (placeholder souřadnice):** čísla 79,5 % / 81 % výše byla nafouknutá
> default souřadnicemi callbooků (viz *Filtrace placeholder / hrubých souřadnic*).
> Po filtraci (kurátorské defaulty + frekvence ≥8 + zahození 4-znakových gridů +
> oprava z jiného zdroje) je **poctivé pokrytí: celý vzorek 74 %, běžní operátoři
> 75 %** (409/544, odhad ~3528 z 4692). Zdroj okresů: QRZ 293, HamQTH 80, QRZCQ 68.

**Závěr:** Tři zdroje zvedly pokrytí běžných operátorů z ~56 % na **75 %**. Limitem
NENÍ odvození okresu, ale **existence profilu** — ~10 % běžných operátorů nemá
veřejný záznam nikde. ~3500 umístitelných běžných operátorů napříč 77 okresy
(průměr ~45/okres) je na choropleth mapu s přehledem.

**Poznámky k interpretaci:**
- Data jsou crowd-sourced a nejspíš zkreslená (aktivní/závodní operátoři se
  registrují častěji) — mapa reprezentuje *dohledatelnou* podmnožinu, ne census.
- HamQTH u části záznamů vrací default souřadnici (~střed ČR); ta se zahazuje a
  okres se pak bere z lokátoru/PSČ/města.
- Část souřadnic pochází z lokátoru (hrubší) — u stanic těsně u hranice okresu
  může dojít k záměně; pro agregované počty zanedbatelné.
- Coverage lze dál zvednout doplněním PSČ→okres a obec→okres číselníku (65 % / 97 %
  nalezených má PSČ / město), případně třetím zdrojem (QRZCQ).

### Okresní znak (pole `district` z HamQTH)

HamQTH vrací pole `district` = okresní znak používaný v závodech (číselník ČRK,
[tabulka okresních znaků](https://ceskyradioklub.cz/provoz/kv/tabulka-okresnich-znaku)).
Je to kanonický identifikátor okresu. Číselník je v `app/okresy.py`
(`okres_from_district()` bere kód `APB` i název okresu ve volném textu;
`okres_map_unit()` sloučí Praha 1–10 na jeden pražský okres pro mapu).

**Měření na vzorku 600 ale ukázalo, že jako zdroj pokrytí nepomáhá:** `district`
je vyplněný jen u ~13 % HamQTH profilů, v nekonzistentním formátu (kódy i volný
text, občas i kraj), a **0 značek** díky němu získalo okres navíc — všechny ho už
měly ze souřadnic. V resolveru je proto zapojen jen jako fallback za
souřadnicemi/gridem. Hlavní hodnota číselníku je pro **popisky a jednotky mapy**
(a jako křížová kontrola).

## Rozhodnutí go/no-go

Pokud je podíl značek s odvozeným okresem dost vysoký, aby mapa dávala smysl
(řádově aspoň desítky %), pokračuje se fází 2 (plný běh + choropleth mapa okresů,
publikují se jen **agregované počty na okres**, nikdy per-callsign data z QRZ —
v souladu s QRZ ToS i s anonymizačním přístupem projektu).

## Poznámka k designu: strategie sběru v plném provozu

Zatím není implementováno — poznámka pro fázi 2.

**Základní model:**
- **Jednorázový plný sběr** na začátku (cache značka→okres). Kvůli dennímu limitu
  QRZ (v POC jsme na ~7500 dotazech narazili na odpojování) ho **rozložit na víc
  dní** — cache je resumovatelná, hotové značky se přeskočí.
- Pak **jen nové značky** — výběr už bere `callsign NOT IN callsign_okres`.
- Mapa = **cache ∩ aktivní značky** (`last_seen = poslední snapshot`), takže
  vypršelé koncese z mapy samy vypadnou; nic se nemaže.

**Latence profilu (důležité):** nově licencovaný operátor ještě nemá veřejný
profil. Současná logika ho jednou dotáže, dostane „nenalezeno", nastaví
`exhausted=1` a **už se k němu nevrátí** — i kdyby si profil později založil.
Řešení = nevzdávat to natrvalo, ale zkoušet znovu s odstupem (schéma má
`fetched_at`):

| Stav značky | Kdy zkusit znovu |
|---|---|
| nová (není v cache) | hned při nejbližším běhu |
| nedohledaná (`okres IS NULL`) | po ~30 dnech, pak periodicky |
| dohledaná (`okres` je) | občas (např. 1×/rok) kvůli **stěhování** (změna okresu) |

Prakticky = rozšířit výběr o `okres IS NULL AND fetched_at < dnes-30dní`
(dnes to umí jen ruční `--retry-failed`, které resetuje všechny naráz). Kadenci
navázat na `INGEST_TIMES`/scheduler, ale pomalu (denně jen nové + „zralé k retry",
ne celý sběr).
