"""FastAPI aplikace: dashboard + JSON API + denní plánovač ingestu."""
import hashlib
import logging
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db, i18n, ingest, stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

_BOT_UA_RE = re.compile(
    r"bot|crawler|spider|slurp|curl|wget|python-requests|httpclient|headless|"
    r"uptime|monitor|kube-probe|pingdom|facebookexternalhit|preview",
    re.IGNORECASE,
)
_CALLSIGN_INPUT_RE = re.compile(r"^(OK|OL)\d+[0-9A-Z]*$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler(timezone="Europe/Prague")
    times = config.ingest_times()
    for hour, minute in times:
        scheduler.add_job(
            ingest.run_ingest,
            "cron",
            hour=hour,
            minute=minute,
            id=f"ingest-{hour:02d}{minute:02d}",
            misfire_grace_time=3600,
        )
    scheduler.start()
    log.info(
        "Plánovač spuštěn – ingest v %s",
        ", ".join(f"{h:02d}:{m:02d}" for h, m in times),
    )
    yield
    scheduler.shutdown()


app = FastAPI(title="ČTÚ Ham Stats", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


def _extract_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


def _country_code_from_headers(request: Request) -> str:
    raw = (
        request.headers.get("cf-ipcountry")
        or request.headers.get("x-country-code")
        or request.headers.get("x-vercel-ip-country")
        or "ZZ"
    )
    code = raw.strip().upper()
    return code if len(code) == 2 and code.isalpha() else "ZZ"


def _visitor_hash(request: Request) -> str:
    ip = _extract_client_ip(request)
    ua = request.headers.get("user-agent", "")
    payload = f"{config.VISIT_HASH_SALT}|{ip}|{ua}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_bot_request(request: Request) -> bool:
    ua = request.headers.get("user-agent", "")
    if not ua:
        return True
    return bool(_BOT_UA_RE.search(ua))


def _track_visit(conn, request: Request) -> None:
    if _is_bot_request(request):
        return

    today = date.today().isoformat()
    seen_at = datetime.now(timezone.utc).isoformat()
    db.record_visit(
        conn,
        visited_on=today,
        visitor_hash=_visitor_hash(request),
        country_code=_country_code_from_headers(request),
        seen_at=seen_at,
    )
    conn.commit()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, lang: str | None = None):
    chosen = i18n.resolve_lang(
        lang,
        request.cookies.get("lang"),
        request.headers.get("accept-language"),
    )
    conn = db.connect()
    try:
        try:
            _track_visit(conn, request)
        except Exception:  # noqa: BLE001
            log.exception("Nepodařilo se zapsat návštěvu")

        data = {
            "summary": stats.summary(conn),
            "series": stats.daily_series(conn),
            "breakdown": stats.breakdown(conn),
            "t": i18n.translations(chosen),
            "lang": chosen,
            "languages": i18n.LANGUAGES,
        }
    finally:
        conn.close()
    response = templates.TemplateResponse(request, "index.html", data)
    if lang in i18n.TRANSLATIONS:
        response.set_cookie("lang", lang, max_age=31536000, samesite="lax")
    return response


@app.get("/api/summary")
def api_summary():
    conn = db.connect()
    try:
        result = stats.summary(conn)
    finally:
        conn.close()
    if result is None:
        raise HTTPException(404, "Zatím žádná data – spusťte ingest.")
    return result


@app.get("/api/daily")
def api_daily(limit: int = Query(365, ge=1, le=3650)):
    conn = db.connect()
    try:
        return stats.daily_series(conn, limit)
    finally:
        conn.close()


@app.get("/api/delta")
def api_delta():
    conn = db.connect()
    try:
        result = stats.daily_delta_details(conn)
    finally:
        conn.close()
    if result is None:
        raise HTTPException(404, "Zatím žádná data – spusťte ingest.")
    return result


@app.get("/api/expiring")
def api_expiring(days: int = Query(30, ge=1, le=730)):
    conn = db.connect()
    try:
        return {
            "days": days,
            "count": stats.expiring_count(conn, days),
            "callsigns": stats.expiring_list(conn, days),
        }
    finally:
        conn.close()


@app.get("/api/breakdown")
def api_breakdown():
    conn = db.connect()
    try:
        result = stats.breakdown(conn)
    finally:
        conn.close()
    if result is None:
        raise HTTPException(404, "Zatím žádná data – spusťte ingest.")
    return result


@app.get("/api/stations")
def api_stations(kind: str = Query(..., pattern="^(unattended|special|club)$")):
    conn = db.connect()
    try:
        stations = stats.station_list(conn, kind)
    finally:
        conn.close()
    return {"kind": kind, "count": len(stations), "callsigns": stations}


@app.get("/api/new-callsigns")
def api_new_callsigns(days: int = Query(30, ge=1, le=730)):
    conn = db.connect()
    try:
        callsigns = stats.new_callsigns_list(conn, days)
    finally:
        conn.close()
    return {"days": days, "count": len(callsigns), "callsigns": callsigns}


@app.get("/api/suggest-callsign")
def api_suggest_callsign(text: str = Query(..., max_length=80), limit: int = Query(12, ge=1, le=20)):
    normalized = stats.normalize_suggestion_seed(text)
    if not normalized:
        raise HTTPException(
            400,
            "Zadejte text obsahující alespoň jedno písmeno bez diakritiky nebo speciálních znaků.",
        )
    conn = db.connect()
    try:
        return stats.suggest_callsigns(conn, text, limit)
    finally:
        conn.close()


@app.get("/api/callsign/{callsign}")
def api_callsign(callsign: str):
    clean = callsign.strip().upper()
    if not clean:
        raise HTTPException(400, "Zadejte volací značku.")
    if not _CALLSIGN_INPUT_RE.fullmatch(clean):
        raise HTTPException(
            400,
            "Neplatný formát značky. Použijte tvar OK/OL + číslice + písmena/číslice.",
        )
    conn = db.connect()
    try:
        return stats.callsign_lookup(conn, clean)
    finally:
        conn.close()


@app.post("/api/ingest")
def api_ingest():
    """Ruční spuštění ingestu (pro první naplnění dat)."""
    try:
        return ingest.run_ingest()
    except Exception as exc:  # noqa: BLE001
        log.exception("Ingest selhal")
        raise HTTPException(502, f"Ingest selhal: {exc}") from exc


@app.get("/visits", response_class=HTMLResponse)
def visits_page(request: Request, lang: str | None = None):
    chosen = i18n.resolve_lang(
        lang,
        request.cookies.get("lang"),
        request.headers.get("accept-language"),
    )
    today = date.today().isoformat()
    conn = db.connect()
    try:
        today_stats = stats.visit_stats_for_day(conn, today)
        last_7 = stats.visit_stats_for_range(conn, 7, today)
        last_365 = stats.visit_stats_for_range(conn, 365, today)
        data = {
            "visit_stats": today_stats,
            "visits_7": last_7,
            "visits_365": last_365,
            "today": today,
            "t": i18n.translations(chosen),
            "lang": chosen,
            "languages": i18n.LANGUAGES,
        }
    finally:
        conn.close()
    response = templates.TemplateResponse(request, "visits.html", data)
    if lang in i18n.TRANSLATIONS:
        response.set_cookie("lang", lang, max_age=31536000, samesite="lax")
    return response


@app.get("/api/visits/today")
def api_visits_today():
    today = date.today().isoformat()
    conn = db.connect()
    try:
        return stats.visit_stats_for_day(conn, today)
    finally:
        conn.close()


@app.get("/api/visits/range")
def api_visits_range(days: int = Query(7, ge=1, le=3650)):
    today = date.today().isoformat()
    conn = db.connect()
    try:
        return stats.visit_stats_for_range(conn, days, today)
    finally:
        conn.close()
