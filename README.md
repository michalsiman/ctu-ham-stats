# STATISTIKY HAM RÁDIA

Webová aplikace zobrazující statistiky radioamatérských individuálních oprávnění (nejen) v ČR nad otevřenými daty Českého telekomunikačního úřadu a externími zdroji.

Adresa aplikace: https://stats.ok1sim.cz/


Zdroj dat: [Individuální oprávnění a volací značky stanic amatérské služby](https://data.ctu.gov.cz/dataset/individualni-opravneni-volaci-znacky-stanic-amaterske-sluzby) (CSV, aktualizace několikrát denně).

## Historie

Aplikace se poprvé objevila v roce 2024 na mé staré doméně vlastni.cloud, kterou jsem ale kompletně zrušil a nechal jsem si jen doménu ok1sim.cz. Původní aplikace si neměla historii i když jsem ji plánoval, takže nebyl vidět trend a změny oproti předchozím období. Nová aplikace už tohle umí.

## Co umí

- denně stáhne a **archivuje** CSV export ČTÚ (surové soubory v `data/archive/`)
- stahuje víckrát denně (ČTÚ data mění i během dne), archiv se neduplikuje, pokud se CSV nezměnilo
- ukládá snapshoty do SQLite a **diffuje je** – CSV neobsahuje datum vydání, takže přírůstky a úbytky lze zjistit jen porovnáváním snapshotů v čase
- dashboard: aktuální počet unikátních volacích značek, denní a měsíční přírůstky/úbytky unikátních značek, počet značek expirujících do 7/30/90 dnů, graf vývoje
- dashboard navíc obsahuje blok „nové značky za 30 dní“ (dle `first_seen` v tabulce `callsigns`, tedy skutečně nově vzniklé unikátní značky, ne pouhé prodloužení)
- dashboard navíc obsahuje blok „Navrhnout novou značku“ se třemi režimy (výběr prefixu `OK`/`OL`, volitelně konkrétní číslice):
  - **ze jména** – skládá příponu z písmen textu (`Novák` → `OK1NOV`…)
  - **v sufixu** – volné značky, jejichž přípona (do 3 písmen) obsahuje zadaný text (`AA` → `OK1AA`, `OK1AAB`, `OK1BAA`)
  - **závodní** – volné short cally `OK`/`OL` + 1 číslice + 1 písmeno (`OK1A`; číslice `0` jen pro `OL`)

  Každý návrh je barevně odlišen podle volnosti: nikdy nepřidělená / po 5leté ochranné lhůtě / nedávno propadlá (odhad z historie, ne jistota)
- jednoduché počítadlo návštěv hlavní stránky: denní unikáty, přehled podle země, souhrn za 7 dní a 365 dní na `/visits`
- JSON API: `/api/summary`, `/api/daily`, `/api/expiring?days=30`, `/api/stations?kind=club`, `/api/callsign/OK1SIM`, `/api/breakdown`
- JSON API nových značek: `/api/new-callsigns?days=30`
- JSON API návrhů značek: `/api/suggest-callsign?text=Novak` (parametry `prefix=OK|OL`, `contest=true` pro závodní short cally, `contains=true` pro značky s textem v příponě). Každý návrh nese `freedom` (never_used / protection_elapsed / recently_lapsed) pro odlišení skutečně volných značek od těch, u nichž ještě běží ochranná lhůta.
- JSON API kandidátů na uvolnění po ochranné lhůtě: `/api/free-after-protection?years=5` – **pravděpodobně volné, ne jistota** (viz níže)
- JSON API návštěvnosti: `/api/visits/today`, `/api/visits/range?days=7`
- **MCP server** na `/mcp` (streamable HTTP, read-only) – AI agent se připojí a dotazuje přes nástroje `overview`, `recent_changes`, `daily_trend`, `suggest_callsign`, `expiring_soon`, `free_after_protection`, `callsign_lookup`

Kandidáti na uvolnění (`/api/free-after-protection`, MCP `free_after_protection`) se vrací vždy s `confidence: "candidate"`: z otevřených dat ČTÚ (bez osobních údajů) nelze odlišit pozdní obnovu původním držitelem od nového přidělení, proto „pravděpodobně volné“, ne „volné“.

Vyhledávání značky má validaci formátu (frontend + backend): musí začínat `OK` nebo `OL`, následovat minimálně jedna číslice a pak volitelně písmena/číslice (`^(OK|OL)\d+[0-9A-Z]*$`). Neplatný vstup se neodesílá na API.
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

## Import historických dat (backfill)

Historii nelze zpětně dohnat ze živého zdroje (CSV nemá datum vydání), ale starší
ručně stažené exporty se dají doplnit jednorázově. Oddělovač (`;` u starších, `,` u
novějších exportů) se autodetekuje. Spouštět **chronologicky od nejstaršího** a vždy
tak, aby datum bylo starší než libovolný už uložený snapshot (jinak se rozbijí
`added/removed` u okolních dat – skript na to upozorní a vyžádá potvrzení):

```bash
python -m app.backfill data/backfill/opravneni_2022-12-15.csv 2022-12-15
python -m app.backfill data/backfill/opravneni_2025-06-06.csv 2025-06-06
```

Den s velkou mezerou k předchozímu snapshotu (backfill / první reálný ingest po něm)
má v `/api/daily` `added/removed = null` a `reconstructed: true`, aby jednorázový
přeskok nezkreslil denní přírůstkovou křivku.

Podrobný postup (pořadí, čistá vs. běžící DB, ověření) je v [docs/BACKFILL.md](docs/BACKFILL.md).

## Konfigurace

| Proměnná | Výchozí | Popis |
|---|---|---|
| `CTU_CSV_URL` | URL exportu ČTÚ | zdrojové CSV |
| `DATA_DIR` | `data` | adresář pro DB a archiv |
| `INGEST_TIMES` | `06:00,14:00` | časy stahování (čárkou oddělené HH:MM) |
| `VISIT_HASH_SALT` | `ctu-ham-stats` | sůl pro anonymní hash návštěvníka (IP + User-Agent) |

Země návštěvníka se bere z proxy hlaviček (`CF-IPCountry`, `X-Country-Code`, `X-Vercel-IP-Country`). Bez nich se uloží `ZZ` (neznámá).

Počítadlo má jednoduchý filtr botů podle `User-Agent` (crawler/spider/bot/monitoring klienti), aby metriky lépe odpovídaly reálným návštěvníkům.

## MCP server

Aplikace vystavuje **MCP server** (Model Context Protocol) na `/mcp` – streamable HTTP,
čistě pull, read-only. AI agent se připojí a dotazuje statistiky přes nástroje:

| Nástroj | Popis |
|---|---|
| `overview` | celkový přehled (jako `/api/summary`) |
| `recent_changes` | poslední změna: přibylo/ubylo značek a jejich seznam |
| `daily_trend` | časová řada denních statistik za N dní |
| `suggest_callsign` | návrh volných značek (režimy: ze jména / `contains` v sufixu / `contest`), s polem `freedom` |
| `expiring_soon` | značky expirující do N dnů |
| `free_after_protection` | kandidáti na uvolnění po ochranné lhůtě (`confidence: candidate`) |
| `callsign_lookup` | historie a stav konkrétní značky |

Vyžaduje balíček `mcp` (v requirements). Když chybí nebo má nekompatibilní API, aplikace
nastartuje i bez `/mcp`. Žádný push/webhook – vyhodnocení událostí (např. hlídání
`free_after_protection`) si řeší agent sám.

**Nasazení za reverzní proxy:** MCP transport má ochranu proti DNS-rebindingu a ve
výchozím stavu povolí jen `localhost` – na veřejné doméně by jinak vracel
`421 Invalid Host header`. Do sekce `[mcp]` v `config.ini` (nebo přes env
`MCP_ALLOWED_HOSTS`) proto uveď veřejné hostname webu, oddělené čárkou:

```ini
[mcp]
allowed_hosts = stats.example.cz
```

Prázdná hodnota ochranu vypne (má-li ji řešit proxy). Klienti se připojují na
`/mcp` (koncové lomítko `/mcp/` je povinné, endpoint na něj přesměruje).

### Připojení AI klienta

Endpoint je **streamable HTTP** MCP, bez autentizace, read-only. URL je
`https://VAS-HOST/mcp/` (s koncovým lomítkem). Veřejná instance:
`https://stats.ok1sim.cz/mcp/`.

**Claude Desktop / Claude Code** – přidej server do konfigurace (`claude_desktop_config.json`,
resp. `claude mcp add`):

```json
{
  "mcpServers": {
    "ctu-ham-stats": {
      "type": "http",
      "url": "https://stats.ok1sim.cz/mcp/"
    }
  }
}
```

Případně přes CLI:

```bash
claude mcp add --transport http ctu-ham-stats https://stats.ok1sim.cz/mcp/
```

**Jiní klienti** (Cursor, Cline, vlastní agent přes `mcp` SDK…) očekávají typ
transportu `http` / `streamable-http` a stejnou URL. Server je bezstavově
dotazovatelný – po `initialize` rovnou volej nástroje z tabulky výše.

**Rychlý test bez klienta** (mělo by vrátit `HTTP 200` a `serverInfo`):

```bash
curl -sS -w '\nHTTP %{http_code}\n' https://stats.ok1sim.cz/mcp/ \
  -X POST -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

## Testy

```bash
pip install -r requirements-dev.txt   # runtime závislosti + pytest
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

## Autoři

- Michal Šiman (OK1SIM)
- Ondřej Koloničný (OK1CDJ)

## Licence

MIT
