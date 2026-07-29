"""Find a market where the signal and the instrument actually coincide.

Two facts came out of testing gold, and they point in opposite directions:

    direction   48.2% same-sign next bar  - no information, on any symbol
    volatility  +0.56 block autocorrelation on gold, +0.02 on R_10

Volatility clusters and direction does not. That is the most robust finding
in empirical finance and it is plainly present here. But gold sells
Touch/No Touch only at daily expiry and offers no vanillas or turbos, while
the synthetics offer every instrument down to 15 seconds and have constant
volatility by construction - nothing to forecast.

So the search is for the overlap: a symbol whose volatility is predictable
AND which sells an instrument that pays for a volatility view intraday.
Without both, a correct forecast has no way to become a position.

`direction_persistence` is measured alongside as a control. It should sit at
roughly 50% everywhere. If a symbol shows a large directional edge, the far
likelier explanation is a data artefact - a stale feed, a repeated candle -
than a market inefficiency, so it is treated as a warning rather than a
discovery.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Sequence

# An instrument can express a volatility view if its payoff depends on how
# far price moves rather than which way. Touch/No Touch is the direct bet;
# vanillas are options, so their price is a function of implied volatility;
# turbos sit in between.
VOL_CATEGORIES = ("touchnotouch", "vanilla", "turbos")


def log_returns(candles: Sequence[dict[str, Any]]) -> list[float]:
    closes = []
    for c in candles:
        try:
            v = float(c["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if v > 0:
            closes.append(v)
    return [math.log(b / a) for a, b in zip(closes, closes[1:])]


def _autocorr(series: Sequence[float]) -> float:
    if len(series) < 4:
        return 0.0
    x, y = series[:-1], series[1:]
    mx, my = statistics.mean(x), statistics.mean(y)
    sx, sy = statistics.pstdev(x), statistics.pstdev(y)
    if sx == 0 or sy == 0:
        return 0.0
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y)) / len(x)
    return cov / (sx * sy)


def vol_persistence(candles: Sequence[dict[str, Any]], block: int = 20) -> float:
    """Does this block's realised volatility predict the next block's?

    Blocks rather than single bars because one bar's absolute return is a
    very noisy volatility estimate; averaging over a block is what makes the
    clustering visible.
    """
    rets = log_returns(candles)
    if len(rets) < block * 4:
        return 0.0
    blocks = [statistics.pstdev(rets[i:i + block])
              for i in range(0, len(rets) - block, block)]
    return _autocorr(blocks)


def direction_persistence(candles: Sequence[dict[str, Any]]) -> float:
    """Fraction of bars whose sign matches the previous bar's.

    The control. ~0.50 is expected and healthy. A large deviation is much
    more likely to be a stale or duplicated feed than a tradeable edge.
    """
    rets = log_returns(candles)
    if len(rets) < 10:
        return 0.5
    signs = [1 if r > 0 else -1 for r in rets]
    same = sum(1 for a, b in zip(signs, signs[1:]) if a == b)
    return same / max(1, len(signs) - 1)


def _minutes(duration: str | None) -> float:
    """Deriv durations look like '5t', '2m', '1d'. Ticks are treated as
    sub-minute so tick contracts count as intraday."""
    if not duration:
        return float("inf")
    unit, num = duration[-1], duration[:-1]
    try:
        n = float(num)
    except ValueError:
        return float("inf")
    return {"t": n / 60, "s": n / 60, "m": n, "h": n * 60,
            "d": n * 1440}.get(unit, float("inf"))


def intraday_vol_instruments(contracts_for_response: dict[str, Any],
                             max_minutes: float = 1440) -> dict[str, str]:
    """Volatility-expressing categories offering a duration under a day.

    Anything requiring a full day cannot act on an intraday forecast, which
    is exactly what blocks gold: its Touch contracts start at 1d.
    """
    found: dict[str, str] = {}
    for entry in contracts_for_response.get("contracts_for", {}).get("available", []):
        cat = entry.get("contract_category")
        if cat not in VOL_CATEGORIES:
            continue
        lo = entry.get("min_contract_duration")
        if _minutes(lo) < max_minutes:
            prev = found.get(cat)
            if prev is None or _minutes(lo) < _minutes(prev):
                found[cat] = lo
    return found


def score(vol_persist: float, instruments: dict[str, str]) -> float:
    """Rank on the overlap, not on either half alone.

    A perfectly forecastable symbol with no intraday instrument scores zero,
    because a forecast that cannot be traded is worth nothing. So does a
    fully-equipped symbol with no predictability.
    """
    if not instruments:
        return 0.0
    return max(0.0, vol_persist)
