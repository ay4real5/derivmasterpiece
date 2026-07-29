"""Which symbol to trade multipliers on, as a number rather than an opinion.

The digit bot chose symbols by quoted house edge, because a binary charges
its margin once per bet and the bet lasts one tick. A multiplier has no
expiry, so that framing does not transfer at all.

THE RESULT THIS MODULE IS BUILT ON. For a driftless random walk bracketed by
a take-profit at distance `tp` and a stop-loss at `sl`, the probability of
hitting TP first is exactly `sl / (tp + sl)`. Multiply out and the expected
PnL of the bracket is zero - it is a fair bet, whatever the symbol, the
volatility or the leverage. So the expected result of a multiplier trade is
**exactly minus the commission**, and no choice of symbol changes that.

What a symbol changes is HOW OFTEN you pay it. A random walk first reaches a
barrier at distance `d` in time proportional to `d^2 / sigma^2`, and `d` here
is the stop-out distance, which is `1 / multiplier`. So:

    cost per day  ~  commission x sigma^2 x multiplier^2

Two of the three squared, which is why the spread between symbols is not
subtle. Measured live at a 50 stake: EUR/USD x100 costs $0.18/day, R_50 x200
costs $58.27/day - a 300x range. Ranking by commission alone gets it wrong:
gold has the cheapest commission of the lot and still costs twice EUR/USD per
day, because it moves four times as fast.

It also makes the leverage decision visible. 400x looks cheap per trade and
is usually wrong, because the multiplier enters the cost SQUARED - it pulls
the stop-out in close, so the position dies and has to be reopened, and each
reopen is another commission.

VOLATILITY IS MEASURED, NEVER ASSUMED. The synthetic names are honest -
R_10 measured 10.1%, R_100 measured 96.6% - but EUR/USD came in at 3.9%
against a textbook 8%, which is the difference between second place and
first. A hardcoded table would have been wrong exactly where it mattered.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Sequence

SECONDS_PER_YEAR = 365 * 24 * 3600


def realised_vol(candles: Sequence[dict[str, Any]], seconds_per_candle: float) -> float:
    """Annualised volatility from candle closes.

    Log returns, population stdev, scaled by sqrt(periods per year). Returns
    0.0 rather than raising when there is not enough data - a symbol with no
    history should rank as unknown, not crash the scan.
    """
    closes = []
    for c in candles:
        try:
            close = float(c["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if close > 0:
            closes.append(close)
    if len(closes) < 3 or seconds_per_candle <= 0:
        return 0.0
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    if not rets:
        return 0.0
    per_year = SECONDS_PER_YEAR / seconds_per_candle
    return statistics.pstdev(rets) * math.sqrt(per_year)


def stop_out_distance(multiplier: float) -> float:
    """Fractional adverse move that wipes the stake: 1/multiplier.

    At 400x that is 0.25%, which is why high leverage churns - the position
    dies inside ordinary noise and every reopen costs another commission.
    """
    if multiplier <= 0:
        raise ValueError("multiplier must be positive")
    return 1.0 / multiplier


def expected_seconds_per_trade(distance: float, vol: float) -> float:
    """Mean first-passage time to a barrier `distance` away, in seconds.

    For a driftless walk the expected time to move `d` is `d^2 / sigma^2`,
    with sigma annualised here so the answer comes out in seconds. Infinite
    when either input is zero: a motionless symbol never resolves, and a
    zero-width barrier is not a trade.
    """
    if distance <= 0 or vol <= 0:
        return float("inf")
    return (distance ** 2) / (vol ** 2) * SECONDS_PER_YEAR


def trades_per_day(distance: float, vol: float) -> float:
    secs = expected_seconds_per_trade(distance, vol)
    return 0.0 if secs == float("inf") else 86400.0 / secs


def cost_per_day(commission: float, distance: float, vol: float) -> float:
    """The ranking number: commission x how often you pay it.

    This is the whole point of the module. Expected PnL from the bracket is
    zero, so a symbol's entire contribution is the rate at which it makes you
    re-pay commission.
    """
    return commission * trades_per_day(distance, vol)


def rank(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score and sort candidates, cheapest per day first.

    Each row needs `symbol`, `commission`, `multiplier`, `vol`. Rows missing
    a usable volatility or commission are kept but sorted last with an
    explicit `unavailable` note, because a symbol that could not be priced is
    a result worth seeing rather than a silent gap.
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        vol = float(r.get("vol") or 0.0)
        commission = r.get("commission")
        mult = float(r.get("multiplier") or 0.0)
        if commission is None or vol <= 0 or mult <= 0:
            out.append({**r, "cost_per_day": float("inf"),
                        "trades_per_day": 0.0, "stop_out_pct": None,
                        "unavailable": r.get("unavailable") or "no volatility or price"})
            continue
        d = stop_out_distance(mult)
        out.append({
            **r,
            "stop_out_pct": d * 100.0,
            "trades_per_day": trades_per_day(d, vol),
            "cost_per_day": cost_per_day(float(commission), d, vol),
            "unavailable": None,
        })
    return sorted(out, key=lambda r: r["cost_per_day"])
