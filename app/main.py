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

from app import db, market

POLL_SECONDS = 300

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


@app.get("/news/{symbol}", response_class=HTMLResponse)
async def news(request: Request, symbol: str):
    symbol = symbol.upper()
    articles = await asyncio.to_thread(market.fetch_news, symbol)
    return templates.TemplateResponse(
        request, "_news.html", {"symbol": symbol, "articles": articles}
    )
