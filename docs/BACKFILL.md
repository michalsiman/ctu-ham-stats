# Návod: import historických dat (backfill)

Tenhle návod je pro správce nasazení. Popisuje, jak do aplikace jednorázově dostat
starší exporty ČTÚ, aby dashboard i statistiky (`freed_after_protection`, „nové
značky", graf vývoje) měly historii až do roku 2022.

## Co máme za data

Tři starší ruční exporty stejného CSV (sloupce se nemění, jen oddělovač u toho
nejstaršího). **Nejsou v gitu** (adresář `data/` je v `.gitignore`) – předávají se
zvlášť. Jsou nenahraditelné (historii nelze zpětně dohnat), proto si je zálohuj.

| Soubor | Oddělovač | Datum snapshotu |
|---|---|---|
| `import_radiove_kmitocty_opravneni-122022.csv` | `;` | `2022-12-15` |
| `import_radiove_kmitocty_opravneni06062025.csv` | `,` | `2025-06-06` |
| `import_radiove_kmitocty_opravneni28082026.csv` | `,` | `2026-08-28` |

> Datum u prosince 2022 je odhad (soubor nese jen měsíc); den 15. je zvolený jako
> střed měsíce. Oddělovač se stejně autodetekuje, datum zadáváš ručně.

## DŮLEŽITÉ: pořadí a proč

`store_snapshot` (kterou backfill používá) počítá `added/removed` proti **bezprostředně
předchozímu** snapshotu a `first_seen` v tabulce `callsigns` nastaví při **prvním**
vložení značky. Proto:

- backfill spouštěj **chronologicky od nejstaršího**;
- a **dřív, než v DB existuje novější snapshot** (typicky před prvním reálným ingestem).

Když vložíš starší datum až po novějším snapshotu, novějšímu zůstane `added/removed`
neaktuální a `first_seen` špatné. Skript to pozná a bez `--yes` se zeptá
(varuje, pokud je zadané datum starší než nejnovější uložený snapshot).

## Postup

### A) Čistá DB (doporučeno)

Nejjistější je postavit DB od nuly. Nepřijdeš o nic, co nejde znovu stáhnout –
živá data se dotáhnou dalším ingestem.

```bash
# 1) soubory na místo
mkdir -p data/backfill
cp <odkud>/import_radiove_kmitocty_opravneni-122022.csv   data/backfill/
cp <odkud>/import_radiove_kmitocty_opravneni06062025.csv  data/backfill/
cp <odkud>/import_radiove_kmitocty_opravneni28082026.csv  data/backfill/

# 2) smaž stávající DB (archiv CSV v data/archive/ nech být)
rm -f data/hamstats.db data/hamstats.db-wal data/hamstats.db-shm

# 3) backfill chronologicky od nejstaršího
python -m app.backfill data/backfill/import_radiove_kmitocty_opravneni-122022.csv  2022-12-15
python -m app.backfill data/backfill/import_radiove_kmitocty_opravneni06062025.csv 2025-06-06
python -m app.backfill data/backfill/import_radiove_kmitocty_opravneni28082026.csv 2026-08-28

# 4) dotáhni aktuální živá data
python -m app.ingest
```

V Dockeru spusť příkazy uvnitř kontejneru. Bind-mount `./data` je namountovaný na
**`/srv/data`** (ne `/srv/app/data`), takže k CSV uváděj **absolutní cestu
`/srv/data/...`** – modul (`-m app.backfill`) se importuje z WORKDIRu `/srv/app`,
ale data leží jinde:

```bash
docker compose exec ham-stats python -m app.backfill /srv/data/backfill/import_radiove_kmitocty_opravneni-122022.csv 2022-12-15
# … zbylé dva soubory stejně …
docker compose exec ham-stats python -m app.ingest
```

### B) Už běžící DB s ostrými daty a BEZ archivu (doporučeno pro server)

Tohle je případ produkčního serveru: běží denní ingest, DB má poslední nasbíraný
měsíc, ale `data/archive/` (raw denní CSV) chybí – takže poslední měsíc existuje
**jen v DB** a postup A) by ho smazal.

Použij **`app.backfill_preserve`**. Vloží staré exporty jako historické snapshoty,
opraví `first_seen` (posune dozadu tam, kde stará data dokládají dřívější
existenci), ale **`last_seen` ani `added/removed` živého období se nedotkne** –
poslední měsíc zůstane přesně jak je. Před zásahem udělá zálohu DB. Je idempotentní.

```bash
# soubory na místo (stačí ty dva starší – novější už pokrývá živý ingest)
mkdir -p data/backfill
cp <odkud>/import_radiove_kmitocty_opravneni-122022.csv   data/backfill/
cp <odkud>/import_radiove_kmitocty_opravneni06062025.csv  data/backfill/

# jeden příkaz, chronologicky od nejstaršího
python -m app.backfill_preserve \
    data/backfill/import_radiove_kmitocty_opravneni-122022.csv 2022-12-15 \
    data/backfill/import_radiove_kmitocty_opravneni06062025.csv 2025-06-06
```

V Dockeru (bind-mount `./data` je uvnitř kontejneru na **`/srv/data`** – proto
absolutní cesty k CSV):

```bash
docker compose exec ham-stats python -m app.backfill_preserve \
    /srv/data/backfill/import_radiove_kmitocty_opravneni-122022.csv 2022-12-15 \
    /srv/data/backfill/import_radiove_kmitocty_opravneni06062025.csv 2025-06-06
```

Skript vypíše `daily_stats` PŘED a PO a zálohu uloží jako `hamstats.db.bak-<čas>`.
Kdyby se něco nezdálo, stačí zálohu vrátit zpět. Pokud omylem zadáš datum, které
už spadá do živého ingestu, skript se zastaví s chybou a nic nezmění.

### C) Už běžící DB, ale archiv MÁŠ

Nejčistší je **znovu postavit podle A)** (nepřijdeš o nic – živá data i archiv se
dotáhnou zpět). Když nechceš, jde vynutit `app.backfill … --yes`, ale
`added/removed` a `first_seen` kolem přechodu pak nebudou přesné – proto radši
A) nebo B).

## Ověření

```bash
sqlite3 data/hamstats.db \
  "SELECT snapshot_date, unique_callsigns, added, removed FROM daily_stats ORDER BY snapshot_date;"
# v Dockeru (DB je uvnitř kontejneru na /srv/data):
# docker compose exec ham-stats sqlite3 /srv/data/hamstats.db \
#   "SELECT snapshot_date, unique_callsigns, added, removed FROM daily_stats ORDER BY snapshot_date;"
```

Čekej ~4 řádky: `2022-12-15`, `2025-06-06`, `2026-08-28` a dnešní ingest.
U `2025-06-06` a `2026-08-28` budou `added/removed` velké (akumulace za dlouhé
období). To je v pořádku – v grafu (`/api/daily`) mají tyto body `reconstructed:
true` a `added/removed: null`, takže denní přírůstkovou křivku nezkreslí.

Rychlá kontrola v aplikaci:

```bash
curl -s "http://localhost:8000/api/daily" | python -m json.tool | grep -E "snapshot_date|reconstructed"
curl -s "http://localhost:8000/api/free-after-protection?years=1"   # měl by vracet kandidáty
```
