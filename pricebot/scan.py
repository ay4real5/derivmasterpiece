"""Rescan swing highs/lows near the current price and write fresh S/R levels.

Used by run_sr_bot.py when price has drifted away from every line in
lines.json for too long - stale levels drawn when price was somewhere else
are not just useless, they burn poll cycles doing nothing. Reuses the same
authenticated websocket the bot already holds (`api.candles`) instead of
opening a second connection.
"""
from __future__ import annotations

from typing import Any, Sequence

TIMEFRAMES = [("1m", 60), ("3m", 180), ("5m", 300), ("10m", 600)]
CLUSTER_PCT = 0.30      # swings within this % of each other are one level
MAX_LEVELS_PER_SIDE = 6


def _cluster(points: list[tuple[int, float]], current: float
            ) -> list[tuple[float, int, float]]:
    """Group nearby swing prices into levels: (price, touches, dist_pct)."""
    if not points:
        return []
    s = sorted(points, key=lambda x: x[1])
    clusters: list[list[tuple[int, float]]] = [[s[0]]]
    for p in s[1:]:
        if abs(p[1] - clusters[-1][-1][1]) / clusters[-1][-1][1] * 100 < CLUSTER_PCT:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    out = []
    for cl in clusters:
        avg = sum(p[1] for p in cl) / len(cl)
        dist = (avg - current) / current * 100
        out.append((avg, len(cl), dist))
    return out


def _swings(candles: Sequence[dict[str, Any]], k: int = 3
           ) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    swing_highs, swing_lows = [], []
    for i in range(k, len(candles) - k):
        if highs[i] == max(highs[i - k:i + k + 1]):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(lows[i - k:i + k + 1]):
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


async def scan_levels(api, symbol: str, tolerance_pct: float = 0.35
                      ) -> list[dict[str, Any]]:
    """Return fresh support/resistance levels near the current price.

    Pulls 500 candles on four timeframes, clusters swing highs/lows, and
    keeps the nearest few on each side of the current price - the same
    approach as the one-off diag_find_sr.py scans, wired to run
    automatically instead of by hand.
    """
    resistances: list[tuple[float, int, float, str]] = []
    supports: list[tuple[float, int, float, str]] = []
    current = None
    for label, gran in TIMEFRAMES:
        candles = await api.candles(symbol, granularity=gran, count=500)
        if not candles:
            continue
        current = float(candles[-1]["close"])
        sh, sl = _swings(candles)
        for price, touches, dist in _cluster(sh, current):
            if dist > 0:
                resistances.append((price, touches, dist, label))
        for price, touches, dist in _cluster(sl, current):
            if dist < 0:
                supports.append((price, touches, dist, label))
    if current is None:
        return []

    resistances.sort(key=lambda x: abs(x[2]))
    supports.sort(key=lambda x: abs(x[2]))
    resistances = resistances[:MAX_LEVELS_PER_SIDE]
    supports = supports[:MAX_LEVELS_PER_SIDE]

    lines = []
    for i, (price, touches, _dist, label) in enumerate(supports, 1):
        lines.append({
            "name": f"S{i}", "symbol": symbol, "price_level": round(price, 4),
            "type": "support", "timeframe": label,
            "tolerance_pct": tolerance_pct, "active": True,
        })
    for i, (price, touches, _dist, label) in enumerate(resistances, 1):
        lines.append({
            "name": f"R{i}", "symbol": symbol, "price_level": round(price, 4),
            "type": "resistance", "timeframe": label,
            "tolerance_pct": tolerance_pct, "active": True,
        })
    return lines
