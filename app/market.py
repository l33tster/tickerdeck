"""yfinance wrappers: quotes, intraday history backfill, and news."""
import logging
import time

import yfinance as yf

log = logging.getLogger("tickerdeck.market")

_daily_cache: dict[tuple[str, str], tuple[float, list]] = {}
DAILY_CACHE_TTL = 600


def fetch_quote(symbol: str) -> dict | None:
    try:
        fi = yf.Ticker(symbol).fast_info
        price = fi.last_price
        prev = fi.previous_close
        if price is None:
            return None
        change_pct = ((price - prev) / prev * 100) if prev else 0.0
        return {"symbol": symbol, "price": float(price), "change_pct": float(change_pct)}
    except Exception:
        log.exception("quote fetch failed for %s", symbol)
        return None


def lookup_name(symbol: str) -> str:
    try:
        info = yf.Ticker(symbol).info
        return info.get("shortName") or info.get("longName") or symbol
    except Exception:
        return symbol


def fetch_intraday(symbol: str) -> list[tuple[float, float]]:
    """(timestamp, close) points for today, 5-minute bars."""
    try:
        hist = yf.Ticker(symbol).history(period="1d", interval="5m")
        return [(idx.timestamp(), float(row["Close"])) for idx, row in hist.iterrows()]
    except Exception:
        log.exception("history fetch failed for %s", symbol)
        return []


def fetch_daily_closes(symbol: str, period: str = "1y") -> list[tuple[float, float]]:
    """(timestamp, close) daily bars, cached for a few minutes per (symbol, period)."""
    key = (symbol, period)
    cached = _daily_cache.get(key)
    if cached and time.time() - cached[0] < DAILY_CACHE_TTL:
        return cached[1]
    try:
        hist = yf.Ticker(symbol).history(period=period, interval="1d")
        points = [(idx.timestamp(), float(row["Close"])) for idx, row in hist.iterrows()]
    except Exception:
        log.exception("daily history fetch failed for %s", symbol)
        points = []
    if points:
        _daily_cache[key] = (time.time(), points)
    return points


def fetch_news(symbol: str, limit: int = 8) -> list[dict]:
    """Latest headlines. Handles both old and new yfinance news schemas."""
    try:
        items = yf.Ticker(symbol).news or []
    except Exception:
        log.exception("news fetch failed for %s", symbol)
        return []
    news = []
    for item in items:
        content = item.get("content", item)
        title = content.get("title")
        url = (content.get("canonicalUrl") or {}).get("url") or content.get("link")
        if not title or not url:
            continue
        provider = (content.get("provider") or {}).get("displayName") or content.get("publisher") or ""
        published = content.get("pubDate") or content.get("displayTime") or ""
        summary = content.get("summary") or ""
        news.append(
            {
                "title": title,
                "url": url,
                "provider": provider,
                "published": str(published)[:16].replace("T", " "),
                "summary": summary[:220],
            }
        )
        if len(news) >= limit:
            break
    return news
