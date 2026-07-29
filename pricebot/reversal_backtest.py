"""Does the mean-reversion signal on Bybit perps survive its own trading cost?

The measurement that prompted this: streak continuation after N same-direction
bars sits at 42.8-48% across every symbol and every horizon tested, not at 50%.
On a linear instrument that looks like a directional edge.

IT IS NOT THE SAME AS A PROFITABLE ONE, and the gap is where most backtests
die. Sign predictability says how OFTEN you are right. Money depends on how
BIG you are right versus how big you are wrong. A signal that reverses 57% of
the time in small increments and continues 43% of the time in large ones loses
money while looking excellent in a win-rate table. So nothing here reports a
win rate without the net return beside it.

THREE ARTEFACTS THAT PRODUCE THIS EXACT PATTERN WITHOUT BEING TRADEABLE, each
checked rather than dismissed:

1. **Bid-ask bounce.** Closes are last-trade prices, so they alternate between
   bid and ask, which manufactures negative autocorrelation and sub-50% streak
   continuation. This is the textbook explanation and it is NOT capturable -
   you pay the spread you would be trying to earn. It is ruled out here by
   magnitude rather than by assertion: bounce contributes roughly
   -s^2/(s^2+sigma^2) to lag-1 autocorrelation, and with a half-spread near
   0.005% against hourly volatility near 0.4%, that is about -0.0002. The
   observed values are 50-300x larger, so bounce cannot account for them.

2. **Fees.** Charged on entry AND exit, every trade, no exceptions. Taker is
   the honest default: a limit order that earns the maker rebate only fills
   when the market comes to you, which on a reversal signal is precisely when
   you were wrong.

3. **Fitting.** The streak length is chosen on the FIRST half of the data and
   then measured once on the second. A parameter picked on all the data and
   reported on all the data is not a result.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Sequence

# Bybit linear perpetual, standard tier. Verify against "My Fee Rates" - tiers
# differ and a stale number here silently flatters every result below.
TAKER_FEE = 0.00055
MAKER_FEE = 0.00020


def bar_returns(candles: Sequence[dict[str, Any]]) -> list[float]:
    """Close-to-close log returns, non-positive prices skipped."""
    closes = [float(c["close"]) for c in candles if float(c.get("close", 0)) > 0]
    return [math.log(b / a) for a, b in zip(closes, closes[1:])]


def simulate_reversal(rets: Sequence[float], streak: int,
                      fee_per_side: float = TAKER_FEE) -> dict[str, Any]:
    """After `streak` same-direction bars, take the OPPOSITE side for one bar.

    Entry and exit at bar closes, which is the only honest choice here: the
    signal is defined by a completed bar, so the earliest possible fill is
    that bar's close. Using the bar's open or its midpoint would be reading a
    price that had not happened when the decision was made.

    Fees are charged on both sides of every trade. Returns are log returns and
    the fee is a simple rate, so they are combined arithmetically - fine at
    these magnitudes and it does not flatter the result.
    """
    if streak < 1:
        raise ValueError("streak must be >= 1")
    trades: list[float] = []
    wins = 0
    gross_total = 0.0
    n = len(rets)
    for i in range(streak, n):
        window = rets[i - streak:i]
        if not window or any(r == 0 for r in window):
            continue
        first = 1 if window[0] > 0 else -1
        if not all((1 if r > 0 else -1) == first for r in window):
            continue
        # Bet AGAINST the run: position is -first for the next bar.
        gross = -first * rets[i]
        net = gross - 2 * fee_per_side
        gross_total += gross
        trades.append(net)
        if net > 0:
            wins += 1
    if not trades:
        return {"streak": streak, "trades": 0, "win_rate": 0.0, "net": 0.0,
                "gross": 0.0, "mean_net": 0.0, "tstat": 0.0, "fee_paid": 0.0}
    net_total = sum(trades)
    mean = net_total / len(trades)
    sd = statistics.pstdev(trades) if len(trades) > 1 else 0.0
    tstat = (mean / (sd / math.sqrt(len(trades)))) if sd > 0 else 0.0
    return {
        "streak": streak,
        "trades": len(trades),
        "win_rate": wins / len(trades),
        "gross": gross_total,
        "net": net_total,
        "mean_net": mean,
        "tstat": tstat,
        "fee_paid": 2 * fee_per_side * len(trades),
    }


def _parkinson(candle: dict[str, Any]) -> float | None:
    """Range volatility for one bar. Same estimator as vol_forecast, local so
    this module has no dependency on the daily-horizon work."""
    try:
        hi, lo = float(candle["high"]), float(candle["low"])
    except (KeyError, TypeError, ValueError):
        return None
    if hi <= 0 or lo <= 0 or hi <= lo:
        return None
    return math.sqrt(math.log(hi / lo) ** 2 / (4.0 * math.log(2.0)))


def vol_forecasts(candles: Sequence[dict[str, Any]],
                  lookback: int = 6) -> list[float | None]:
    """Volatility forecast at each bar, using ONLY that bar and earlier.

    Aligned to `candles`, so `out[t]` is what you would have known at the
    close of bar `t`. Any leakage here would be invisible and would make the
    whole filtered result meaningless, which is why the alignment is asserted
    in the tests rather than trusted to reading.
    """
    out: list[float | None] = [None] * len(candles)
    vols = [_parkinson(c) for c in candles]
    for t in range(len(candles)):
        window = [v for v in vols[max(0, t - lookback + 1): t + 1] if v is not None]
        out[t] = statistics.fmean(window) if len(window) >= 2 else None
    return out


def causal_rank(values: Sequence[float | None], t: int) -> float | None:
    """Quantile rank of values[t] among values BEFORE t. Fully causal.

    A fixed threshold taken from the whole sample would leak the future, and
    one taken from a training half drifts out of date as volatility regimes
    change. Ranking against only the past does neither.
    """
    cur = values[t]
    if cur is None or t < 30:
        return None
    prior = [v for v in values[:t] if v is not None]
    if len(prior) < 30:
        return None
    return sum(1 for v in prior if v < cur) / len(prior)


def reversal_by_vol_bucket(candles: Sequence[dict[str, Any]], streak: int,
                           lookback: int = 6,
                           buckets: int = 4) -> list[dict[str, Any]]:
    """Gross edge per trade, split by forecast-volatility bucket.

    The diagnostic that decides whether filtering can work AT ALL. The fee is
    a fixed rate, so the only way a filter helps is if the gross edge is
    LARGER when volatility is higher. If the gross edge per trade is flat
    across buckets, no threshold will rescue it and there is nothing to build.
    """
    fc = vol_forecasts(candles, lookback)
    rows: list[list[float]] = [[] for _ in range(buckets)]
    moves: list[list[float]] = [[] for _ in range(buckets)]
    for t in range(streak, len(candles) - 1):
        rank = causal_rank(fc, t)
        if rank is None:
            continue
        signs = []
        ok = True
        for j in range(t - streak + 1, t + 1):
            a, b = float(candles[j - 1]["close"]), float(candles[j]["close"])
            if a <= 0 or b <= 0 or a == b:
                ok = False
                break
            signs.append(1 if b > a else -1)
        if not ok or len(set(signs)) != 1:
            continue
        a, b = float(candles[t]["close"]), float(candles[t + 1]["close"])
        if a <= 0 or b <= 0:
            continue
        nxt = math.log(b / a)
        idx = min(buckets - 1, int(rank * buckets))
        rows[idx].append(-signs[0] * nxt)
        moves[idx].append(abs(nxt))
    out = []
    for i in range(buckets):
        if not rows[i]:
            out.append({"bucket": i, "trades": 0, "gross_per_trade": 0.0,
                        "typical_move": 0.0})
            continue
        out.append({"bucket": i, "trades": len(rows[i]),
                    "gross_per_trade": statistics.fmean(rows[i]),
                    "typical_move": statistics.median(moves[i])})
    return out


def simulate_reversal_filtered(candles: Sequence[dict[str, Any]], streak: int,
                               min_vol_rank: float = 0.5, lookback: int = 6,
                               fee_per_side: float = TAKER_FEE) -> dict[str, Any]:
    """The reversal trade, taken only when forecast volatility ranks high.

    Everything is causal: the streak comes from closed bars, the volatility
    forecast uses only bars up to the decision, and its threshold is a rank
    against the past alone. Entry and exit are bar closes.
    """
    fc = vol_forecasts(candles, lookback)
    trades: list[float] = []
    gross_total = 0.0
    wins = 0
    for t in range(streak, len(candles) - 1):
        rank = causal_rank(fc, t)
        if rank is None or rank < min_vol_rank:
            continue
        signs = []
        ok = True
        for j in range(t - streak + 1, t + 1):
            a, b = float(candles[j - 1]["close"]), float(candles[j]["close"])
            if a <= 0 or b <= 0 or a == b:
                ok = False
                break
            signs.append(1 if b > a else -1)
        if not ok or len(set(signs)) != 1:
            continue
        a, b = float(candles[t]["close"]), float(candles[t + 1]["close"])
        if a <= 0 or b <= 0:
            continue
        gross = -signs[0] * math.log(b / a)
        net = gross - 2 * fee_per_side
        gross_total += gross
        trades.append(net)
        if net > 0:
            wins += 1
    if not trades:
        return {"streak": streak, "min_vol_rank": min_vol_rank, "trades": 0,
                "win_rate": 0.0, "gross": 0.0, "net": 0.0, "mean_net": 0.0,
                "tstat": 0.0}
    net_total = sum(trades)
    mean = net_total / len(trades)
    sd = statistics.pstdev(trades) if len(trades) > 1 else 0.0
    return {"streak": streak, "min_vol_rank": min_vol_rank,
            "trades": len(trades), "win_rate": wins / len(trades),
            "gross": gross_total, "net": net_total, "mean_net": mean,
            "tstat": (mean / (sd / math.sqrt(len(trades)))) if sd > 0 else 0.0}


def break_even_move(fee_per_side: float = TAKER_FEE) -> float:
    """How far price must move your way just to cover the round trip.

    The number that decides whether a horizon is viable at all. Compare it
    against the typical bar move: if the fee is a large fraction of what the
    bar does, no signal on that timescale can pay.
    """
    return 2 * fee_per_side


def bounce_autocorrelation(half_spread: float, vol_per_bar: float) -> float:
    """Lag-1 autocorrelation that bid-ask bounce ALONE would produce.

    Used to rule the artefact in or out by size rather than by hand-waving.
    Returns a negative number; compare its magnitude with the observed value.
    """
    if vol_per_bar <= 0:
        return 0.0
    s2 = half_spread ** 2
    return -s2 / (s2 + vol_per_bar ** 2)


def split_sample(rets: Sequence[float]) -> tuple[list[float], list[float]]:
    """First half to choose on, second half to be judged on."""
    h = len(rets) // 2
    return list(rets[:h]), list(rets[h:])


def choose_streak(rets: Sequence[float], candidates: Sequence[int] = (2, 3, 4, 5),
                  fee_per_side: float = TAKER_FEE) -> int:
    """Best streak length BY MEAN NET RETURN on the data given.

    Deliberately takes only the sample it is handed, so the caller must pass
    the training half. Selecting on everything and reporting on everything is
    the commonest way a backtest lies.
    """
    best, best_mean = candidates[0], -math.inf
    for k in candidates:
        r = simulate_reversal(rets, k, fee_per_side)
        if r["trades"] >= 30 and r["mean_net"] > best_mean:
            best, best_mean = k, r["mean_net"]
    return best
