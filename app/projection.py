"""Monte Carlo price projection using geometric Brownian motion.

Calibrated on historical daily log returns. This is a statistical
simulation of plausible ranges given past volatility — not a forecast.
"""
import zlib

import numpy as np

MIN_HISTORY = 60
TRADING_DAYS_PER_YEAR = 252


def simulate(closes: list[float], days: int, paths: int = 500, seed_key: str = "") -> dict | None:
    if len(closes) < MIN_HISTORY:
        return None
    prices = np.asarray(closes, dtype=float)
    rets = np.diff(np.log(prices))
    mu, sigma = float(rets.mean()), float(rets.std())
    # Deterministic seed per (symbol, horizon) so repeat views are stable.
    rng = np.random.default_rng(zlib.crc32(seed_key.encode()))
    shocks = rng.normal(mu, sigma, size=(paths, days))
    sim = prices[-1] * np.exp(np.cumsum(shocks, axis=1))
    return {
        "p10": np.percentile(sim, 10, axis=0).tolist(),
        "p50": np.percentile(sim, 50, axis=0).tolist(),
        "p90": np.percentile(sim, 90, axis=0).tolist(),
        "vol_annual": sigma * TRADING_DAYS_PER_YEAR ** 0.5,
    }
