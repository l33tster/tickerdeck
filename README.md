# tickerdeck

A small self-hosted stock watchlist dashboard. Prices and sparklines at a glance;
click any ticker to see its latest news. Built as a hobby project for a
"normal investor" level of detail — no clutter, no login, one container.

![stack](https://img.shields.io/badge/FastAPI-htmx-4f8ef7) ![data](https://img.shields.io/badge/data-Yahoo%20Finance-6f42c1)

## Features

- **Watchlist table** — price, day change %, and an SVG sparkline per ticker
- **Click a ticker → latest news** headlines with source and summary (htmx panel)
- **Background poller** stores quotes in SQLite every 5 minutes, so trends build up over time
- **Add/remove tickers** from the UI; new tickers are backfilled with intraday history
- **Single container** — FastAPI + SQLite, no build step, no API keys

## Run it

```bash
docker compose up --build
# open http://localhost:8000
```

Or locally without Docker:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## How it works

```
yfinance (Yahoo Finance) ──► background poller (every 5 min)
                                   │
                                   ▼
                              SQLite (data/)
                                   │
FastAPI + Jinja2 ◄─────────────────┘
      │
      ▼
htmx frontend — table polls /rows every 60s; row click loads /news/{symbol}
```

- `app/market.py` — yfinance wrappers (quotes, intraday backfill, news)
- `app/db.py` — SQLite schema and queries
- `app/main.py` — routes, poller lifecycle, sparkline SVG renderer
- `templates/` — one page plus two htmx partials

## Notes

- Quotes are delayed (Yahoo Finance) — fine for watching, not for trading.
- The SQLite file lives in `./data/` and is volume-mounted, so history survives restarts.
- Not investment advice; hobby project.
