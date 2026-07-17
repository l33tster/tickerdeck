"""SQLite storage for the watchlist, cached quotes, and price history."""
import os
import sqlite3
import time

DB_PATH = os.environ.get("DB_PATH", "data/tickerdeck.db")

DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "SPY"]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol   TEXT PRIMARY KEY,
                name     TEXT,
                added_at REAL
            );
            CREATE TABLE IF NOT EXISTS quotes (
                symbol     TEXT PRIMARY KEY,
                price      REAL,
                change_pct REAL,
                updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS prices (
                symbol TEXT,
                ts     REAL,
                price  REAL,
                PRIMARY KEY (symbol, ts)
            );
            """
        )
        if conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO watchlist (symbol, name, added_at) VALUES (?, ?, ?)",
                [(s, s, time.time()) for s in DEFAULT_TICKERS],
            )


def watchlist_entries() -> list[tuple[str, str]]:
    with connect() as conn:
        rows = conn.execute("SELECT symbol, name FROM watchlist ORDER BY symbol").fetchall()
    return [(r["symbol"], r["name"] or r["symbol"]) for r in rows]


def watchlist_symbols() -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT symbol FROM watchlist ORDER BY symbol").fetchall()
    return [r["symbol"] for r in rows]


def add_symbol(symbol: str, name: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (symbol, name, added_at) VALUES (?, ?, ?)",
            (symbol, name, time.time()),
        )


def remove_symbol(symbol: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
        conn.execute("DELETE FROM quotes WHERE symbol = ?", (symbol,))
        conn.execute("DELETE FROM prices WHERE symbol = ?", (symbol,))


def symbols_needing_names() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT symbol FROM watchlist WHERE name IS NULL OR name = symbol"
        ).fetchall()
    return [r["symbol"] for r in rows]


def set_name(symbol: str, name: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE watchlist SET name = ? WHERE symbol = ?", (name, symbol))


def symbols_needing_history() -> list[str]:
    """Watchlist symbols with fewer than 2 stored price points (no sparkline yet)."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT w.symbol FROM watchlist w
               LEFT JOIN (SELECT symbol, COUNT(*) AS c FROM prices GROUP BY symbol) p
                 ON p.symbol = w.symbol
               WHERE COALESCE(p.c, 0) < 2"""
        ).fetchall()
    return [r["symbol"] for r in rows]


def upsert_quote(symbol: str, price: float, change_pct: float) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO quotes (symbol, price, change_pct, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                 price = excluded.price,
                 change_pct = excluded.change_pct,
                 updated_at = excluded.updated_at""",
            (symbol, price, change_pct, time.time()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO prices (symbol, ts, price) VALUES (?, ?, ?)",
            (symbol, time.time(), price),
        )


def insert_history(symbol: str, points: list[tuple[float, float]]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO prices (symbol, ts, price) VALUES (?, ?, ?)",
            [(symbol, ts, price) for ts, price in points],
        )


def dashboard_rows() -> list[dict]:
    """Watchlist joined with cached quotes and the recent price series."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT w.symbol, w.name, q.price, q.change_pct, q.updated_at
               FROM watchlist w LEFT JOIN quotes q ON q.symbol = w.symbol
               ORDER BY w.symbol"""
        ).fetchall()
        out = []
        for r in rows:
            series = conn.execute(
                """SELECT price FROM (
                     SELECT ts, price FROM prices WHERE symbol = ?
                     ORDER BY ts DESC LIMIT 80
                   ) ORDER BY ts ASC""",
                (r["symbol"],),
            ).fetchall()
            out.append(dict(r) | {"series": [s["price"] for s in series]})
    return out
