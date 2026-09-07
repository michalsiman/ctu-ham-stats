# Předání dat okres/kraj autorovi projektu

Tenhle dokument popisuje, jak z lokálně vyřešených okresů (viz
[QRZ_POC.md](QRZ_POC.md)) vyrobit **CSV** a jak ho autor naimportuje do hlavní
DB pro mapu. Kód resolveru (QRZ/HamQTH/QRZCQ) běží jen u toho, kdo má přístupy
ke callbookům; autor dostane **hotová anonymizovaná data**, ne přístupy.

## Co se předává

CSV `callsign_okres.csv` se sloupci:

| sloupec | příklad | popis |
|---|---|---|
| `callsign` | `OK1ABC` | značka |
| `okres` | `Brno-město` | název okresu |
| `okres_lau` | `CZ0642` | LAU kód okresu (dřív NUTS4) |
| `kraj` | `Jihomoravský kraj` | název kraje |
| `kraj_nuts` | `CZ064` | NUTS3 kód kraje |
| `kraj_iso` | `CZ-64` | ISO 3166-2 kód kraje (klíč pro geoBoundaries) |

**Privacy-by-design:** v CSV je výhradně `značka→okres→kraj` + kanonické kódy.
Žádné jméno, adresa, PSČ, souřadnice ani lokátor – ta se do DB vůbec neukládají
(viz *Ochrana soukromí* v QRZ_POC.md). Mapa publikuje jen agregované počty.

Rozsah: **všechny značky s odvozeným okresem** (`okres IS NOT NULL`), bez ohledu
na aktivitu. Filtraci na aktuálně platné značky si dělá autor ve své DB přes
`last_seen` (viz dotaz níže) – proto se posílá kompletní vyřešená cache.

## Export (u toho, kdo má resolvovaná data)

```bash
python scripts/export_okres_csv.py          # -> data/callsign_okres.csv
python scripts/export_okres_csv.py -o /tmp/callsign_okres.csv --db data/hamstats.db
```

Okresy, které skript neumí napojit na kraj, **nezahazuje tiše** – vypíše je do
logu (pak stačí doplnit alias do `app/kraje.py` a export zopakovat).

## Import (u autora, do hlavní DB)

```bash
# napřed nanečisto – jen spočítá dopad, nic nezapíše
python scripts/import_okres_csv.py callsign_okres.csv --db data/hamstats.db --dry-run

# ostrý import
python scripts/import_okres_csv.py callsign_okres.csv --db data/hamstats.db
```

Import vytvoří (když chybí) samostatnou tabulku **`callsign_region`**:

```sql
CREATE TABLE callsign_region (
    callsign  TEXT PRIMARY KEY,
    okres     TEXT NOT NULL,
    okres_lau TEXT NOT NULL,
    kraj      TEXT NOT NULL,
    kraj_nuts TEXT NOT NULL,
    kraj_iso  TEXT NOT NULL
);
```

- `INSERT OR REPLACE` podle značky → **idempotentní**, opakovaný import přepíše.
- **Nic nemaže**; značky mimo CSV v tabulce zůstanou.
- Tabulka je nezávislá na zbytku schématu – nevyžaduje resolver ani jiné migrace.

## Dotaz pro mapu

Krajová choropleth (jen aktuálně platné značky):

```sql
SELECT r.kraj_iso, r.kraj, COUNT(*) AS pocet
FROM callsign_region r
JOIN callsigns c ON c.callsign = r.callsign
WHERE c.last_seen = (SELECT MAX(last_seen) FROM callsigns)
GROUP BY r.kraj_iso
ORDER BY pocet DESC;
```

Až mapa dostane okresní vrstvu, stejná data nesou i `okres` / `okres_lau` –
stačí seskupit přes ně, nic se nereřeší.
