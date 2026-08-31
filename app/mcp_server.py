"""MCP server nad statistikami ČTÚ – čistě pull, read-only nástroje.

AI agent se připojí přes MCP (streamable HTTP na /mcp) a dotazuje se na
statistiky. Žádný push/webhook – vyhodnocení událostí (např. porovnání
`free_after_protection` s vlastní pamětí) je věcí agenta.

Import je verzně odolný: SDK `mcp` v2 přejmenovalo `FastMCP` na `MCPServer`.
Zkoušíme obojí; když balíček chybí nebo má nekompatibilní API, `main.py`
import odchytí a aplikace nastartuje bez /mcp.
"""
from mcp.server.transport_security import TransportSecuritySettings

from . import config, db, masking, stats

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp < 2
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server("ctu-ham-stats")


def _transport_security() -> TransportSecuritySettings:
    """Ochrana /mcp proti DNS-rebindingu podle configu.

    SDK jinak (host=127.0.0.1) povolí jen localhost a za reverzní proxy vrací
    na veřejné doméně 421 „Invalid Host header". Do allowlistu proto přidáme
    hostname z configu; localhost necháme vždy (lokální vývoj, health-check).
    Prázdný `allowed_hosts` v configu = ochranu vypnout (řeší proxy).
    """
    hosts = config.mcp_allowed_hosts()
    if not hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    localhost = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]:*"]
    origins = [scheme + h for h in hosts for scheme in ("https://", "http://")]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts + localhost,
        allowed_origins=origins,
    )


@mcp.tool()
def suggest_callsign(
    text: str = "",
    prefix: str = "OK",
    digit: str | None = None,
    contest: bool = False,
    contains: bool = False,
    limit: int = 20,
) -> dict:
    """Navrhne volné volací značky OK/OL.

    Režimy (vzájemně výlučné):
    - výchozí: skládá kandidáty z písmen zadaného `text` (např. jméno).
    - `contest=True`: volné závodní značky PREFIX + číslice + 1 písmeno
      (např. OK1A, OL5T); `text` se neřeší.
    - `contains=True`: volné značky, jejichž přípona (do 3 písmen) OBSAHUJE
      `text` (např. AA → OK1AA, OK1AAB, OK1BAA).
    `digit` omezí návrh na jednu číslici.

    Každý návrh nese `freedom`: never_used (jistě volná) / protection_elapsed
    (po 5leté ochranné lhůtě) / recently_lapsed (lhůta běží, původní držitel
    může obnovit) – odhad z historie, ne jistota.
    """
    conn = db.connect()
    try:
        if contest:
            result = stats.suggest_contest_callsigns(conn, prefix, digit, limit)
        elif contains:
            result = stats.suggest_by_suffix_contains(conn, text, prefix, digit, limit)
        else:
            result = stats.suggest_callsigns(conn, text, limit, digit, prefix)
        return masking.mask_data(result)
    finally:
        conn.close()


@mcp.tool()
def overview() -> dict:
    """Celkový přehled: počet unikátních značek, změna proti minulému a
    předchozímu měsíci, srovnání penetrace radioamatérů ČR vs. Německo."""
    conn = db.connect()
    try:
        return masking.mask_data(stats.summary(conn) or {})
    finally:
        conn.close()


@mcp.tool()
def recent_changes() -> dict:
    """Poslední aktualizace dat: datum snapshotu, kolik značek od minula
    přibylo/ubylo a jejich seznam."""
    conn = db.connect()
    try:
        return masking.mask_data(stats.daily_delta_details(conn) or {})
    finally:
        conn.close()


@mcp.tool()
def daily_trend(days: int = 30) -> list[dict]:
    """Časová řada denních statistik (unikátní značky, added/removed) za
    posledních `days` dní. Body s velkou mezerou (backfill) mají added/removed
    null a `reconstructed=true`."""
    conn = db.connect()
    try:
        return masking.mask_data(stats.daily_series(conn, days))
    finally:
        conn.close()


@mcp.tool()
def expiring_soon(days: int = 30) -> dict:
    """Značky, kterým vyprší platnost do `days` dnů."""
    conn = db.connect()
    try:
        return masking.mask_data({
            "days": days,
            "count": stats.expiring_count(conn, days),
            "callsigns": stats.expiring_list(conn, days),
        })
    finally:
        conn.close()


@mcp.tool()
def free_after_protection(years: int = 5, include_occasional: bool = False) -> dict:
    """Kandidáti na uvolnění po ochranné lhůtě – POZOR, není to jistota
    (`confidence: "candidate"`). Z otevřených dat ČTÚ bez osobních údajů nelze
    odlišit pozdní obnovu původním držitelem od nového přidělení."""
    conn = db.connect()
    try:
        result = stats.freed_after_protection(conn, years, include_occasional)
        return masking.mask_data({"years": years, "count": len(result), "callsigns": result})
    finally:
        conn.close()


@mcp.tool()
def longest_expired(limit: int = 20, include_occasional: bool = False) -> dict:
    """Značky, jejichž platnost vypršela nejdříve (nejdéle „mrtvé") a které už
    nejsou v posledních datech – seřazené od nejstarší expirace.

    POZOR: archiv začíná až od `archive_since`, takže jde o nejstarší
    POZOROVATELNÉ expirace, ne nutně nejstarší v realitě. `include_occasional=
    True` přidá i příležitostné/speciální značky (jinak vynechány)."""
    conn = db.connect()
    try:
        result = stats.longest_expired(conn, limit, include_occasional)
        return masking.mask_data({
            "archive_since": stats.earliest_snapshot(conn),
            "count": len(result),
            "callsigns": result,
        })
    finally:
        conn.close()


@mcp.tool()
def new_callsigns(days: int = 30) -> dict:
    """Seznam nově vzniklých značek za posledních `days` dní (poprvé se
    objevily v datech). Značky z prvního dne archivu se nezapočítávají –
    historii dozadu nelze dohnat."""
    conn = db.connect()
    try:
        callsigns = stats.new_callsigns_list(conn, days)
        return masking.mask_data({"days": days, "count": len(callsigns), "callsigns": callsigns})
    finally:
        conn.close()


@mcp.tool()
def breakdown() -> dict:
    """Rozložení aktuálních značek zvlášť pro OK a OL: počty podle prefixu,
    podle číslice za prefixem a podle délky přípony."""
    conn = db.connect()
    try:
        return masking.mask_data(stats.breakdown(conn) or {})
    finally:
        conn.close()


@mcp.tool()
def stations(kind: str) -> dict:
    """Seznam značek daného druhu (abecedně, s max. platností na značku).

    `kind`:
    - `unattended` – neobsluhovaná zařízení (převaděče, majáky…): OK + číslo 0
    - `special` – speciální/příležitostné značky (víceciferné číslo, např. OL700…)
    - `club` – klubové stanice (OK1/OK2 + tři písmena začínající K/O/R)
    """
    kind = kind.strip().lower()
    valid_kinds = ("unattended", "special", "club")
    if kind not in valid_kinds:
        return {"error": "neplatný kind", "valid_kinds": list(valid_kinds)}
    conn = db.connect()
    try:
        result = stats.station_list(conn, kind)
        return masking.mask_data({"kind": kind, "count": len(result), "callsigns": result})
    finally:
        conn.close()


@mcp.tool()
def callsign_lookup(callsign: str) -> dict:
    """Historie a stav konkrétní volací značky."""
    conn = db.connect()
    try:
        return masking.mask_data(stats.callsign_lookup(conn, callsign.strip().upper()))
    finally:
        conn.close()


# Postaví se hned při importu, aby vznikl session_manager (potřebný v lifespanu
# main.py). Endpoint je na kořeni sub-appky, mount v main.py ho dá pod /mcp.
asgi_app = mcp.streamable_http_app(
    streamable_http_path="/",
    transport_security=_transport_security(),
)
