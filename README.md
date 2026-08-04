# ČTÚ Ham Stats

Webová aplikace zobrazující statistiky radioamatérských individuálních oprávnění v ČR nad otevřenými daty Českého telekomunikačního úřadu.

Adresa aplikace: https://stats.ok1sim.cz/


Zdroj dat: [Individuální oprávnění a volací značky stanic amatérské služby](https://data.ctu.gov.cz/dataset/individualni-opravneni-volaci-znacky-stanic-amaterske-sluzby) (CSV, aktualizace několikrát denně).

## Co umí

- denně stáhne a **archivuje** CSV export ČTÚ (surové soubory v `data/archive/`)
- stahuje víckrát denně (ČTÚ data mění i během dne), archiv se neduplikuje, pokud se CSV nezměnilo
- ukládá snapshoty do SQLite a **diffuje je** – CSV neobsahuje datum vydání, takže přírůstky a úbytky lze zjistit jen porovnáváním snapshotů v čase
- dashboard: aktuální počet platných oprávnění a unikátních volacích značek, denní přírůstky/úbytky, počet značek expirujících do 30 a 90 dnů, graf vývoje
- JSON API: `/api/summary`, `/api/daily`, `/api/expiring?days=30`, `/api/stations?kind=club`, `/api/callsign/OK1SIM`, `/api/breakdown`
- vícejazyčné rozhraní: čeština, angličtina, němčina, francouzština (přepínač vpravo nahoře)

Expirace se počítá z **maximální** platnosti na značku – prodloužené oprávnění se v datech objeví jako nový řádek s pozdějším datem, takže se prodloužené značky nepočítají jako expirující.

## Spuštění (lokálně)

```bash
pip install -r requirements.txt
python -m app.ingest                  # první stažení dat
uvicorn app.main:app --reload         # http://localhost:8000
```

## Spuštění (Docker)

```bash
docker compose up -d --build
curl -X POST http://localhost:8000/api/ingest   # první naplnění dat
```

Kontejner pak sám stahuje data v 6:00 a ve 14:00 (nastavitelné přes `INGEST_TIMES`, klidně i víc časů). Data (SQLite + archiv CSV) jsou v bind-mountovaném adresáři `./data`.

## Konfigurace

| Proměnná | Výchozí | Popis |
|---|---|---|
| `CTU_CSV_URL` | URL exportu ČTÚ | zdrojové CSV |
| `DATA_DIR` | `data` | adresář pro DB a archiv |
| `INGEST_TIMES` | `06:00,14:00` | časy stahování (čárkou oddělené HH:MM) |

## Testy

```bash
python -m pytest tests/
```

## Překlady

Texty jsou v `app/i18n.py`. Jazyk se vybírá v pořadí `?lang=xx` → cookie → hlavička
`Accept-Language` → čeština. Nový jazyk = nový klíč v `TRANSLATIONS` se stejnými
klíči jako `cs`; chybějící klíče se automaticky doplní z češtiny. Test v
`tests/test_i18n.py` hlídá, že žádnému jazyku klíč nechybí.

## Poznámky k datům

- CSV obsahuje sloupce `ID, Volací značka, Číslo reference, Platnost do` – nic víc (žádné datum vydání, třída, ani osobní údaje)
- jedna značka může mít víc řádků (víc oprávnění / prodloužení)
- historie se zpětně nedá dohnat – proto archiv od prvního dne

## Licence

MIT
