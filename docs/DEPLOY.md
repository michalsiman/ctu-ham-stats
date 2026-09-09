# Nasazení: historie + okresy na produkční server (Docker)

Runbook pro správce nasazení. Popisuje, jak do běžící Docker instance dostat
**historická data ČTÚ** (backfill) a **dohledávání okresu** ke značkám přes
callbooky (QRZ/HamQTH/QRZCQ) pro choropleth mapu.

Navazuje na [BACKFILL.md](BACKFILL.md) (detaily importu historie) a
[QRZ_POC.md](QRZ_POC.md) (jak resolver funguje + ochrana soukromí).

## Pořadí ve zkratce

1. Doplnit dvě mezery v Dockeru (jinak okres úloha spadne)
2. Nahrát soubory mimo git (historická CSV, geodata, creds)
3. Backfill historie do běžící DB
4. Initial naplnění okresů (rychle z hotové cache) + zapnout denní údržbu
5. Ověřit

---

## 0. Co si vzít s sebou (věci, které **nejsou v gitu**)

Vše leží v lokálním `data/` (adresář je v `.gitignore`):

| Co | Zdroj (lokálně) | Kam na serveru |
|---|---|---|
| Historické exporty ČTÚ | `data/backfill/*.csv` | `./data/backfill/` |
| Geodata resolveru | `data/geo/okresy.geojson`, `psc_okres.csv`, `obce_okresy.csv` | `./data/geo/` |
| Hotová okres-cache (aby ses vyhnul tisícům dotazů na QRZ) | `data/hamstats.db` (tabulka `callsign_okres`) | `./data/seed.db` (dočasně) |
| Callbook přístupy | `config.local.ini` | `./config.local.ini` |

`./data` je v compose bind-mount na `/srv/data`, takže cokoli do něj nakopíruješ,
kontejner rovnou vidí.

---

## 1. Dvě mezery v Dockeru – doplnit PŘED okresy

Backfill historie funguje out-of-the-box (moduly `app.backfill*` jsou v image).
Okres úloha vyžaduje dvě věci navíc – **obojí je v repu už ošetřené**, ale musíš
o nich vědět:

- **`scripts/` v image** – `app/okres.py` importuje `scripts.qrz_poc` za běhu.
  `Dockerfile` proto obsahuje `COPY scripts ./scripts`. (Bez něj by denní job i
  `POST /api/okres` spadly na `ModuleNotFoundError: No module named 'scripts'`.)
- **`config.local.ini` v kontejneru** – v `docker-compose.yml` je připravený
  (zakomentovaný) mount:
  ```yaml
  # - ./config.local.ini:/srv/app/config.local.ini:ro
  ```
  **Odkomentuj ho až poté, co soubor na serveru existuje** – jinak Docker vyrobí
  místo něj prázdný adresář a start spadne. Bez creds se okres úloha stejně
  nezaregistruje, zbytek aplikace běží normálně.

Po úpravách přestav a nastartuj:

```bash
docker compose up -d --build
```

---

## 2. Přístupy ke callbookům

Resolver zkouší tři zdroje v pořadí **QRZ → HamQTH → QRZCQ** (fallbacky doplňují
pokrytí). Přístupy patří **jen** do `config.local.ini` (je v `.gitignore`; do
verzovaného `config.ini` hesla nikdy):

```ini
[qrz]
username = OK1XXX
password = ****
agent = ctu-ham-stats

[hamqth]
username = OK1XXX
password = ****

[qrzcq]
username = OK1XXX
password = ****
```

| Zdroj | Účet | Poznámka |
|---|---|---|
| **QRZ.com** | placené **XML/Logbook Data** předplatné (ne běžný účet) | povinný – bez něj se job nezaregistruje |
| **HamQTH.com** | zdarma po registraci | fallback, session 1 h; vypnutí `--no-hamqth` |
| **QRZCQ.com** | samostatný **premium** účet (login z QRZ/HamQTH tam neplatí) | fallback, přidal ~12 p.b.; vypnutí `--no-qrzcq` |

Alternativa bez souboru: proměnné prostředí `QRZ_USERNAME`/`QRZ_PASSWORD`,
`HAMQTH_*`, `QRZCQ_*` (env má vždy přednost před ini). Dej je do `env_file`
mimo git, ne přímo do `docker-compose.yml`.

> **Privacy-by-design:** do DB (`callsign_okres`) jde výhradně dvojice
> **značka→okres** + neosobní metadata (zdroj, metoda, čas). Jméno, adresa, PSČ,
> souřadnice ani lokátor se neukládají – zpracují se jen v paměti a zahodí.
> Publikují se pouze agregované počty na okres. Viz *Ochrana soukromí* v
> [QRZ_POC.md](QRZ_POC.md).

---

## 3. Backfill historie (běžící server bez archivu)

Produkční server má živá data z denního ingestu, ale nemá `data/archive/` (raw
denní CSV) → použij **`app.backfill_preserve`**. Vloží staré snapshoty, opraví
`first_seen`, ale živého období (`last_seen`, `added/removed`) se nedotkne;
před zásahem udělá zálohu DB a je idempotentní.

```bash
docker compose exec ham-stats python -m app.backfill_preserve \
    data/backfill/import_radiove_kmitocty_opravneni-122022.csv 2022-12-15 \
    data/backfill/import_radiove_kmitocty_opravneni06062025.csv 2025-06-06
```

Nejnovější export (např. `28082026`) neposílej – ten už pokrývá živý ingest.
Varianta pro čistou DB a další detaily jsou v [BACKFILL.md](BACKFILL.md).

Ověření:

```bash
docker compose exec ham-stats sqlite3 data/hamstats.db \
  "SELECT snapshot_date, unique_callsigns, added, removed FROM daily_stats ORDER BY snapshot_date;"
```

---

## 4. Okresy: rychlé initial naplnění + denní údržba

Fronta dohledávání jede nad tabulkou `callsign_okres` (`okres IS NULL`, řazeno
dle `fetched_at`). Pokud už máš lokálně vyřešené značky, **naseeduj cache z
lokální DB** místo opětovného bušení na QRZ (~5000 dotazů, denní limit). Denní
job pak jen dobírá nové/nedohledané.

### 4a. Naseeduj `callsign_okres` z lokální DB

Zkopíruj lokální `data/hamstats.db` na server jako `./data/seed.db`, pak:

```bash
docker compose exec ham-stats sqlite3 data/hamstats.db "
  ATTACH 'data/seed.db' AS seed;
  INSERT OR REPLACE INTO callsign_okres SELECT * FROM seed.callsign_okres;
  DETACH seed;"
rm data/seed.db
```

Přenese se i stav `fetched_at`/`exhausted`, takže fronta už vyřešené značky
znovu nedotazuje.

> Alternativa přes CSV (`scripts/import_okres_csv.py`) naplní jen
> `callsign_region` (mapu), ne `callsign_okres` – fronta by pak dohledávala
> všechno znovu. Pro server je proto lepší kopie tabulky výše.

### 4b. Promítni cache do mapy

Naplní `callsign_region` z `callsign_okres` a udělá i malý sweep nových značek:

```bash
docker compose exec ham-stats curl -s -X POST http://localhost:8000/api/okres
```

### 4c. Denní automatika

Sekce `[okres]` v `config.ini` (job běží uvnitř kontejneru přes APScheduler;
zaregistruje se při startu, pokud `enabled=true` a jsou QRZ creds):

```ini
[okres]
enabled = true
times = 03:30      ; brzy ráno, mimo ingest (INGEST_TIMES=06:00,14:00)
days = *
slice = 150        ; nových/nedohledaných značek za běh (round-robin dle fetched_at)
chunk = 100        ; průběžné ukládání po N značkách
sleep = 0.5        ; pauza mezi dotazy (throttling)
qrz_count_stop = 7000   ; strop 24h QRZ lookupů – gracefully zastaví, zbytek příště
```

Nové značky se doberou postupně (`slice`/den); vypršelé koncese z mapy samy
vypadnou (mapa = cache ∩ aktivní značky), nic se nemaže.

---

## 5. Ověření

```bash
# počty pro mapu (jen dohledané aktivní značky)
docker compose exec ham-stats curl -s http://localhost:8000/api/geo | python -m json.tool | head

# je okres job naplánovaný? (hledej "Plánovač – okres lookup")
docker compose logs ham-stats | grep -i okres
```

Na webu se v textovém přehledu naplní `.geo-district-val` a mapa se podbarví
(choropleth), tooltip ukáže počet značek v okrese.

---

## 6. Aktualizace verze na serveru

Kód aplikace (`app/`, `scripts/`) se do image **kopíruje při buildu**, není
bind-mount. Proto samotný `git pull` novou verzi nenasadí – běžel by dál starý
image. Po stažení kódu je vždy potřeba **rebuild**.

Autor na to má skript (`~/update.sh` nebo podobně):

```bash
#!/bin/bash
set -e
cd ~/ctu-ham-stats
git pull
docker compose up -d --build
docker compose logs --tail 10
```

`--build` je nutné – bez něj poběží starý image (např. bez `scripts/` → okres
úloha padá). `git pull` sám image nepřestaví.

> **Pozor na `set -e` + `git pull`:** když má `git pull` konflikt (typicky kvůli
> lokálně odkomentovanému mountu `config.local.ini` v `docker-compose.yml`, viz
> níže), skript kvůli `set -e` **spadne a rebuild neproběhne**. Aby byl pull vždy
> bezkonfliktní, drž verzovaný `docker-compose.yml` beze změn a creds předávej
> přes `env_file` (viz níže).

### Co vyžaduje rebuild a co ne

| Změna | Stačí |
|---|---|
| `app/`, `scripts/`, `Dockerfile`, `requirements.txt` | `git pull` + `docker compose up -d --build` |
| `config.ini` | `docker compose restart` (bind-mount, ale čte se při startu) |
| `config.local.ini`, cokoli v `./data/` | čte se živě; u config stačí `docker compose restart` |

### Pozor na lokální úpravy verzovaných souborů

`config.local.ini` **není v gitu**, takže ho `git pull` nepřinese – nakopíruj ho
ručně (krok 2). Jakmile kvůli němu odkomentuješ mount v `docker-compose.yml`, je
to lokální změna verzovaného souboru → příští `git pull` může hlásit konflikt.
Možnosti:

- **doporučeno (kvůli auto-update skriptu):** creds předat přes `env_file` mimo
  `docker-compose.yml` (proměnné `QRZ_USERNAME`/`QRZ_PASSWORD`, `HAMQTH_*`,
  `QRZCQ_*`) – verzovaný compose se pak nemění a `git pull` je vždy bezkonfliktní;
- nebo nechat mount jako lokální změnu a při pullu ji řešit ručně (`git stash` →
  `git pull` → `git stash pop`) – u `set -e` skriptu ale znamená každý update
  ruční zásah.

### Ověření po updatu

```bash
docker compose ps                         # kontejner běží (Up)
docker compose logs --tail=50 ham-stats   # bez chyb při startu
```

Data (`./data/hamstats.db`) i nasbíraná okres-cache rebuild **přežijí** – jsou
v bind-mountu mimo image.
