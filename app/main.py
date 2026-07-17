"""tickerdeck — a small stock watchlist dashboard with click-through news."""
import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from datetime import datetime, timezone

from app import charts, db, market, projection

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    task = asyncio.create_task(poller())
    yield
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


def render_panel(request: Request, symbol: str, tab: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_panel.html", {"symbol": symbol, "tab": tab, **ctx}
    )


def _date_label(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %Y")


@app.get("/panel/{symbol}/news", response_class=HTMLResponse)
async def panel_news(request: Request, symbol: str):
    symbol = symbol.upper()
    articles = await asyncio.to_thread(market.fetch_news, symbol)
    return render_panel(request, symbol, "news", articles=articles)


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
    return render_panel(request, symbol, "chart", **ctx)


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
    return render_panel(request, symbol, "projection", **ctx)
