"""Server-side SVG charts: history line chart and projection fan chart."""

W, H = 480, 240
PAD_L, PAD_R, PAD_T, PAD_B = 48, 54, 12, 24


def _y(v: float, lo: float, hi: float) -> float:
    span = (hi - lo) or 1.0
    return PAD_T + (1 - (v - lo) / span) * (H - PAD_T - PAD_B)


def _x(i: float, n: int) -> float:
    return PAD_L + i / max(n - 1, 1) * (W - PAD_L - PAD_R)


def _fmt(v: float) -> str:
    return f"${v:,.0f}" if v >= 100 else f"${v:,.2f}"


def _frame(lo: float, hi: float, x_labels: list[tuple[float, str, str]]) -> list[str]:
    parts = []
    for v in (lo, (lo + hi) / 2, hi):
        y = _y(v, lo, hi)
        parts.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}"'
            f' stroke="var(--border)" stroke-width="1"/>'
        )
        parts.append(f'<text x="{PAD_L - 6}" y="{y + 3.5:.1f}" text-anchor="end" class="axis">{_fmt(v)}</text>')
    for frac, label, anchor in x_labels:
        x = PAD_L + frac * (W - PAD_L - PAD_R)
        parts.append(f'<text x="{x:.1f}" y="{H - 6}" text-anchor="{anchor}" class="axis">{label}</text>')
    return parts


def _svg(parts: list[str]) -> str:
    return f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>'


def line_chart(closes: list[float], start_label: str, end_label: str) -> str:
    lo, hi = min(closes), max(closes)
    n = len(closes)
    pts = " ".join(f"{_x(i, n):.1f},{_y(v, lo, hi):.1f}" for i, v in enumerate(closes))
    color = "var(--up)" if closes[-1] >= closes[0] else "var(--down)"
    parts = _frame(lo, hi, [(0, start_label, "start"), (1, end_label, "end")])
    parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8"/>')
    return _svg(parts)


def fan_chart(
    history: list[float],
    p10: list[float],
    p50: list[float],
    p90: list[float],
    start_label: str,
    horizon_label: str,
) -> str:
    n = len(history) + len(p50)
    lo = min(min(history), min(p10))
    hi = max(max(history), max(p90))
    today_x = _x(len(history) - 1, n)
    today_y = _y(history[-1], lo, hi)

    hist_pts = " ".join(f"{_x(i, n):.1f},{_y(v, lo, hi):.1f}" for i, v in enumerate(history))
    upper = [(_x(len(history) + i, n), _y(v, lo, hi)) for i, v in enumerate(p90)]
    lower = [(_x(len(history) + i, n), _y(v, lo, hi)) for i, v in enumerate(p10)]
    band = [(today_x, today_y), *upper, *reversed(lower), (today_x, today_y)]
    band_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in band)
    median_pts = f"{today_x:.1f},{today_y:.1f} " + " ".join(
        f"{_x(len(history) + i, n):.1f},{_y(v, lo, hi):.1f}" for i, v in enumerate(p50)
    )

    today_frac = (len(history) - 1) / (n - 1)
    parts = _frame(
        lo, hi,
        [(0, start_label, "start"), (today_frac, "today", "middle"), (1, horizon_label, "end")],
    )
    parts.append(f'<polygon points="{band_pts}" fill="var(--accent)" opacity="0.16"/>')
    parts.append(
        f'<line x1="{today_x:.1f}" y1="{PAD_T}" x2="{today_x:.1f}" y2="{H - PAD_B}"'
        f' stroke="var(--muted)" stroke-width="1" stroke-dasharray="3 3" opacity="0.6"/>'
    )
    parts.append(f'<polyline points="{hist_pts}" fill="none" stroke="var(--text)" stroke-width="1.6"/>')
    parts.append(
        f'<polyline points="{median_pts}" fill="none" stroke="var(--accent)"'
        f' stroke-width="1.8" stroke-dasharray="5 4"/>'
    )
    label_x = W - PAD_R + 4
    for series in (p90, p50, p10):
        parts.append(
            f'<text x="{label_x}" y="{_y(series[-1], lo, hi) + 3.5:.1f}" class="axis">{_fmt(series[-1])}</text>'
        )
    return _svg(parts)
