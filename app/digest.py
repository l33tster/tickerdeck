"""Morning digest: prior-day performance + news across the watchlist,
ranked by a transparent attention score. A briefing, not investment advice."""
import logging

from app import db, market

log = logging.getLogger("tickerdeck.digest")

POSITIVE = {
    "beat", "beats", "surge", "surges", "soar", "soars", "rally", "rallies",
    "record", "upgrade", "upgraded", "raise", "raises", "growth", "profit",
    "jump", "jumps", "gain", "gains", "strong", "tops", "bullish", "win",
    "wins", "approval", "approves", "expands", "partnership",
}
NEGATIVE = {
    "miss", "misses", "fall", "falls", "drop", "drops", "plunge", "plunges",
    "cut", "cuts", "downgrade", "downgraded", "lawsuit", "probe", "recall",
    "warn", "warns", "warning", "loss", "losses", "weak", "slump", "bearish",
    "layoffs", "fraud", "halt", "halts", "delay", "delays", "risk",
}

# Attention score weights — tune to taste. Bigger move + more coverage = higher.
W_DAY, W_NEWS, W_WEEK = 1.5, 1.0, 0.5


def _tone(text: str) -> str:
    words = set(text.lower().replace(",", " ").replace(".", " ").replace("'", " ").split())
    pos, neg = len(words & POSITIVE), len(words & NEGATIVE)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def build() -> dict:
    """Collect per-ticker stats + news and rank by attention score."""
    rows = []
    for symbol, name in db.watchlist_entries():
        closes = market.fetch_daily_closes(symbol, "1mo")
        if len(closes) < 2:
            log.warning("digest: skipping %s, not enough history", symbol)
            continue
        prices = [c for _, c in closes]
        day = (prices[-1] - prices[-2]) / prices[-2] * 100
        week = (prices[-1] - prices[-6]) / prices[-6] * 100 if len(prices) >= 6 else 0.0
        articles = market.fetch_news(symbol, limit=8)
        for a in articles:
            a["tone"] = _tone(f'{a["title"]} {a["summary"]}')
        tones = [a["tone"] for a in articles]
        if tones.count("positive") > tones.count("negative"):
            coverage = "coverage leans positive"
        elif tones.count("negative") > tones.count("positive"):
            coverage = "coverage leans negative"
        else:
            coverage = "coverage is mixed"

        reasons = [f"moved {day:+.1f}% on the last session"]
        if abs(week) >= 2:
            reasons.append(f"{week:+.1f}% over the week")
        if articles:
            reasons.append(f"{len(articles)} fresh headlines, {coverage}")

        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "close": prices[-1],
                "day": day,
                "week": week,
                "news": articles[:2],
                "news_count": len(articles),
                "score": abs(day) * W_DAY + len(articles) * W_NEWS + abs(week) * W_WEEK,
                "reasons": reasons,
            }
        )

    rows.sort(key=lambda r: r["day"], reverse=True)
    top = sorted(rows, key=lambda r: r["score"], reverse=True)[:3]
    return {"rows": rows, "top": top}


def as_text(data: dict) -> str:
    lines = ["tickerdeck morning brief", ""]
    lines.append("Worth a look first:")
    for r in data["top"]:
        lines.append(f"  {r['symbol']} — " + "; ".join(r["reasons"]))
    lines.append("")
    lines.append("Watchlist:")
    for r in data["rows"]:
        lines.append(
            f"  {r['symbol']:<6} ${r['close']:>9,.2f}  day {r['day']:+6.2f}%  week {r['week']:+6.2f}%  ({r['news_count']} headlines)"
        )
    lines += ["", "Automated summary of your watchlist from public data (Yahoo Finance).",
              "Not investment advice."]
    return "\n".join(lines)
