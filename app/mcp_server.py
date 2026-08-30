"""MCP server nad statistikami ČTÚ – čistě pull, read-only nástroje.

AI agent se připojí přes MCP (streamable HTTP na /mcp) a dotazuje se na
statistiky. Žádný push/webhook – vyhodnocení událostí (např. porovnání
`free_after_protection` s vlastní pamětí) je věcí agenta.

Import je verzně odolný: SDK `mcp` v2 přejmenovalo `FastMCP` na `MCPServer`.
Zkoušíme obojí; když balíček chybí nebo má nekompatibilní API, `main.py`
import odchytí a aplikace nastartuje bez /mcp.
"""
from . import db, masking, stats

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp < 2
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server("ctu-ham-stats")


@mcp.tool()
def suggest_callsign(
    text: str = "",
    prefix: str = "OK",
    digit: str | None = None,
    contest: bool = False,
    limit: int = 20,
) -> dict:
    """Navrhne volné volací značky OK/OL.

    Běžně skládá kandidáty z písmen zadaného textu. Při `contest=True` místo
    toho vypíše volné závodní značky tvaru PREFIX + číslice + 1 písmeno
    (např. OK1A, OL5T); `text` se pak neřeší. `digit` omezí návrh na jednu
    číslici.
    """
    conn = db.connect()
    try:
        if contest:
            result = stats.suggest_contest_callsigns(conn, prefix, digit, limit)
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
def callsign_lookup(callsign: str) -> dict:
    """Historie a stav konkrétní volací značky."""
    conn = db.connect()
    try:
        return masking.mask_data(stats.callsign_lookup(conn, callsign.strip().upper()))
    finally:
        conn.close()


# Postaví se hned při importu, aby vznikl session_manager (potřebný v lifespanu
# main.py). Endpoint je na kořeni sub-appky, mount v main.py ho dá pod /mcp.
asgi_app = mcp.streamable_http_app(streamable_http_path="/")
