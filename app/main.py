"""tickerdeck — a small stock watchlist dashboard with click-through news."""
import asyncio
import contextlib
import logging
import os
import time
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from datetime import datetime, timedelta, timezone

from app import charts, db, digest, emailer, market, projection

POLL_SECONDS = 300

CHART_PERIODS = [("1mo", "1M"), ("6mo", "6M"), ("1y", "1Y")]
HORIZONS = [(63, "3M"), (126, "6M"), (252, "1Y")]

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tickerdeck")

templates = Jinja2Templates(directory="templates")


def sparkline(series: list[float], width: int = 120, height: int = 32) -> str:
    """Inline SVG polyline for a price series."""
    if len(series) < 2:
        return ""
    lo, hi = min(series), max(series)
    span = (hi - lo) or 1.0
    pad = 2
    step = (width - 2 * pad) / (len(series) - 1)
    points = " ".join(
        f"{pad + i * step:.1f},{height - pad - (v - lo) / span * (height - 2 * pad):.1f}"
        for i, v in enumerate(series)
    )
    color = "var(--up)" if series[-1] >= series[0] else "var(--down)"
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
        f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>'
    )


templates.env.globals["sparkline"] = sparkline


def refresh_quotes() -> None:
    for symbol in db.watchlist_symbols():
        quote = market.fetch_quote(symbol)
        if quote:
            db.upsert_quote(quote["symbol"], quote["price"], quote["change_pct"])
    log.info("quotes refreshed")


def bootstrap() -> None:
    """One-time fill-in: real company names for seeded tickers, and intraday
    history so sparklines aren't empty on a fresh database."""
    for symbol in db.symbols_needing_names():
        db.set_name(symbol, market.lookup_name(symbol))
    for symbol in db.symbols_needing_history():
        db.insert_history(symbol, market.fetch_intraday(symbol))


async def poller() -> None:
    try:
        await asyncio.to_thread(bootstrap)
    except Exception:
        log.exception("bootstrap failed")
    while True:
        try:
            await asyncio.to_thread(refresh_quotes)
        except Exception:
            log.exception("poll cycle failed")
        await asyncio.sleep(POLL_SECONDS)


def _digest_html(data: dict, date_label: str) -> str:
    return templates.env.get_template("digest.html").render(
        rows=data["rows"], top=data["top"], date_label=date_label
    )


def send_digest() -> None:
    tz = ZoneInfo(os.environ.get("DIGEST_TZ", "America/New_York"))
    date_label = datetime.now(tz).strftime("%A, %B %d, %Y")
    data = digest.build()
    if not data["rows"]:
        log.warning("digest: no data, skipping send")
        return
    emailer.send(
        f"tickerdeck morning brief — {date_label}",
        _digest_html(data, date_label),
        digest.as_text(data),
    )
    log.info("digest sent to %s", os.environ.get("DIGEST_TO"))


def _seconds_until_next_digest() -> float:
    tz = ZoneInfo(os.environ.get("DIGEST_TZ", "America/New_York"))
    hh, mm = (os.environ.get("DIGEST_TIME", "07:30").split(":") + ["0"])[:2]
    weekdays_only = os.environ.get("DIGEST_DAYS", "weekdays") != "daily"
    now = datetime.now(tz)
    candidate = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    while candidate <= now or (weekdays_only and candidate.weekday() >= 5):
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


async def digest_scheduler() -> None:
    if not emailer.is_configured():
        log.warning("digest: SMTP not configured — morning email disabled (see .env.example)")
        return
    while True:
        wait = _seconds_until_next_digest()
        log.info("digest: next send in %.1f hours", wait / 3600)
        await asyncio.sleep(wait)
        try:
            await asyncio.to_thread(send_digest)
        except Exception:
            log.exception("digest send failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    tasks = [asyncio.create_task(poller()), asyncio.create_task(digest_scheduler())]
    yield
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="tickerdeck", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


def render_rows(request: Request) -> HTMLResponse:
    rows = db.dashboard_rows()
    stale = [r["symbol"] for r in rows if not r["updated_at"]]
    return templates.TemplateResponse(
        request, "_rows.html", {"rows": rows, "now": time.time(), "stale": bool(stale)}
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/rows", response_class=HTMLResponse)
def rows(request: Request):
    return render_rows(request)


@app.post("/watchlist", response_class=HTMLResponse)
async def add_ticker(request: Request, symbol: str = Form(...)):
    symbol = symbol.strip().upper()
    if symbol and symbol not in db.watchlist_symbols():
        quote = await asyncio.to_thread(market.fetch_quote, symbol)
        if quote:
            name = await asyncio.to_thread(market.lookup_name, symbol)
            db.add_symbol(symbol, name)
            db.upsert_quote(quote["symbol"], quote["price"], quote["change_pct"])
            history = await asyncio.to_thread(market.fetch_intraday, symbol)
            db.insert_history(symbol, history)
    return render_rows(request)


@app.delete("/watchlist/{symbol}", response_class=HTMLResponse)
def remove_ticker(request: Request, symbol: str):
    db.remove_symbol(symbol.upper())
    return render_rows(request)


@app.get("/digest", response_class=HTMLResponse)
async def digest_preview():
    """Browser preview of the morning email."""
    tz = ZoneInfo(os.environ.get("DIGEST_TZ", "America/New_York"))
    data = await asyncio.to_thread(digest.build)
    return HTMLResponse(_digest_html(data, datetime.now(tz).strftime("%A, %B %d, %Y")))


@app.post("/digest/send", response_class=HTMLResponse)
async def digest_send_now():
    """Manual trigger, mostly for testing SMTP setup."""
    try:
        await asyncio.to_thread(send_digest)
    except Exception as exc:  # surface config errors to the browser
        return HTMLResponse(f"<pre>Send failed: {exc}</pre>", status_code=500)
    return HTMLResponse("<pre>Digest sent.</pre>")


def render_panel(request: Request, symbol: str, tab: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_panel.html", {"symbol": symbol, "tab": tab, **ctx}
    )


def _date_label(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %Y")


def _remember(response: HTMLResponse, **cookies: str) -> HTMLResponse:
    """Persist the panel view so switching tickers keeps the same tab open."""
    for key, value in cookies.items():
        response.set_cookie(key, value, max_age=86400 * 30, path="/")
    return response


@app.get("/panel/{symbol}", response_class=HTMLResponse)
async def panel(request: Request, symbol: str):
    """Row click: open whichever tab (and period/horizon) was last active."""
    tab = request.cookies.get("panel_tab", "news")
    if tab == "chart":
        return await panel_chart(request, symbol, request.cookies.get("chart_period", "6mo"))
    if tab == "projection":
        try:
            days = int(request.cookies.get("proj_days", "126"))
        except ValueError:
            days = 126
        return await panel_projection(request, symbol, days)
    return await panel_news(request, symbol)


@app.get("/panel/{symbol}/news", response_class=HTMLResponse)
async def panel_news(request: Request, symbol: str):
    symbol = symbol.upper()
    articles = await asyncio.to_thread(market.fetch_news, symbol)
    return _remember(
        render_panel(request, symbol, "news", articles=articles), panel_tab="news"
    )


@app.get("/panel/{symbol}/chart", response_class=HTMLResponse)
async def panel_chart(request: Request, symbol: str, period: str = "6mo"):
    symbol = symbol.upper()
    if period not in dict(CHART_PERIODS):
        period = "6mo"
    points = await asyncio.to_thread(market.fetch_daily_closes, symbol, period)
    ctx: dict = {"periods": CHART_PERIODS, "period": period, "svg": None}
    if len(points) >= 2:
        closes = [c for _, c in points]
        ctx["svg"] = charts.line_chart(closes, _date_label(points[0][0]), "today")
        ctx["high"], ctx["low"] = max(closes), min(closes)
        ctx["change"] = (closes[-1] - closes[0]) / closes[0] * 100
    return _remember(
        render_panel(request, symbol, "chart", **ctx),
        panel_tab="chart", chart_period=period,
    )


@app.get("/panel/{symbol}/projection", response_class=HTMLResponse)
async def panel_projection(request: Request, symbol: str, days: int = 126):
    symbol = symbol.upper()
    if days not in dict(HORIZONS):
        days = 126
    horizon_label = dict(HORIZONS)[days]
    points = await asyncio.to_thread(market.fetch_daily_closes, symbol, "1y")
    closes = [c for _, c in points]
    sim = projection.simulate(closes, days, seed_key=f"{symbol}:{days}")
    ctx: dict = {"horizons": HORIZONS, "days": days, "svg": None}
    if sim:
        history = closes[-90:]
        ctx["svg"] = charts.fan_chart(
            history, sim["p10"], sim["p50"], sim["p90"],
            _date_label(points[-len(history)][0]), f"+{horizon_label}",
        )
        current, median = closes[-1], sim["p50"][-1]
        ctx.update(
            current=current,
            median=median,
            median_pct=(median - current) / current * 100,
            band_lo=sim["p10"][-1],
            band_hi=sim["p90"][-1],
            vol=sim["vol_annual"] * 100,
            horizon_label=horizon_label,
        )
    return _remember(
        render_panel(request, symbol, "projection", **ctx),
        panel_tab="projection", proj_days=str(days),
    )
