"""FastAPI aplikace: dashboard + JSON API + denní plánovač ingestu."""
import logging
from contextlib import asynccontextmanager
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


@app.get("/", response_class=HTMLResponse)
def index(request: Request, lang: str | None = None):
    chosen = i18n.resolve_lang(
        lang,
        request.cookies.get("lang"),
        request.headers.get("accept-language"),
    )
    conn = db.connect()
    try:
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


@app.get("/api/callsign/{callsign}")
def api_callsign(callsign: str):
    if not callsign.strip():
        raise HTTPException(400, "Zadejte volací značku.")
    conn = db.connect()
    try:
        return stats.callsign_lookup(conn, callsign)
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
