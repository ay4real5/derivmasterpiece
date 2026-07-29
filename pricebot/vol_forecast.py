"""Is tomorrow's volatility predictable from today's, on markets we can trade?

The synthetics are settled: TICK_ANALYSIS.md shows no exploitable structure at
any horizon. Real markets are different - volatility clustering there is the
most reproduced result in empirical finance. But an edge is only an edge if an
instrument exists to express it, and the census in scratch_instruments.json is
blunt about what Deriv actually sells:

    crypto            multipliers ONLY - and multiplier EV is minus the
                      commission at ANY volatility, so a vol forecast is
                      worth exactly nothing there
    forex, metals     Touch/No Touch, minimum duration 1 DAY
    stock indices     Touch/No Touch, minimum duration 7 DAYS
    everything real   no vanillas, no turbos, nothing intraday

So the forecast horizon is not ours to choose. It is one day, because that is
the shortest volatility contract that exists on a market with real volatility
dynamics. That single fact is why this module measures persistence at a ONE
DAY block and nowhere else.

THE MISTAKE THIS MODULE EXISTS TO AVOID. Earlier in this project I reported
gold volatility persistence of +0.56 and treated it as forecastability. It was
not. Re-measuring across sample lengths gave -0.069 at 30 days, +0.088 at 63,
+0.140 at 126 and +0.548 at 316 - a number that grows with the window, which
is the signature of slow regime drift being picked up by a long sample, not of
a day-ahead forecast. A correlation with no p-value, no sample size and no
horizon attached is not a measurement. Everything here carries all three, plus
an out-of-sample split, because that is what caught the error.

ESTIMATOR CHOICE MATTERS FOR POWER. Deriv serves only ~260 daily candles - one
year - so the sample is fixed and small, and the estimator is the only lever
left. Close-to-close volatility from a single daily return is extremely noisy;
the Parkinson high-low estimator uses the whole day's range and has roughly
FIVE times the efficiency, which is worth more here than any amount of
cleverness downstream. Noise in the estimator biases persistence measurements
DOWNWARD, so a weak result from a noisy estimator is ambiguous while a weak
result from a good one is informative.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Sequence

from .tick_stats import autocorrelation

# Parkinson's constant: Var[ln(H/L)] = 4 ln2 * sigma^2 for a driftless
# Brownian motion observed over one period.
_PARKINSON = 1.0 / (4.0 * math.log(2.0))


def parkinson_vol(candle: dict[str, Any]) -> float | None:
    """Range-based volatility for one candle, or None if the range is unusable.

    Returns the standard deviation for the period, not annualised - the
    comparison here is one day against the next, so a common scale factor
    would cancel and only add a chance to get it wrong.
    """
    try:
        hi, lo = float(candle["high"]), float(candle["low"])
    except (KeyError, TypeError, ValueError):
        return None
    if hi <= 0 or lo <= 0 or hi < lo:
        return None
    if hi == lo:
        # A genuinely flat day is far more likely to be a stale feed than a
        # market that did not move; zero would drag the mean down and inflate
        # persistence, so it is dropped rather than trusted.
        return None
    return math.sqrt(_PARKINSON * math.log(hi / lo) ** 2)


def vol_series(candles: Sequence[dict[str, Any]]) -> list[float]:
    """Per-candle Parkinson volatility, unusable candles dropped."""
    out = []
    for c in candles:
        v = parkinson_vol(c)
        if v is not None:
            out.append(v)
    return out


def close_to_close_vol(candles: Sequence[dict[str, Any]]) -> list[float]:
    """|log return| per candle - the noisy estimator, kept as a cross-check.

    If the two estimators disagree about whether persistence exists, the
    disagreement itself is the finding and neither number should be quoted.
    """
    closes = []
    for c in candles:
        try:
            v = float(c["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if v > 0:
            closes.append(v)
    return [abs(math.log(b / a)) for a, b in zip(closes, closes[1:])]


def persistence(vols: Sequence[float], lag: int = 1) -> dict[str, Any]:
    """Does this period's volatility predict the next period's?

    Reported on LOG volatility. Volatility is strongly right-skewed, so a
    handful of crisis days dominate a raw correlation and can manufacture
    persistence out of two nearby spikes. Logs make the series roughly
    symmetric, which is the standard treatment and the conservative one.
    """
    v = [math.log(x) for x in vols if x > 0]
    out = autocorrelation(v, lag)
    out["n"] = len(v)
    out["measure"] = "log_parkinson_vol"
    return out


def split_half(vols: Sequence[float], lag: int = 1) -> dict[str, Any]:
    """The same measurement on each half, which is what catches drift.

    A real day-ahead effect holds in both halves at a similar size. A number
    produced by slow regime drift concentrates in whichever half contains the
    regime change and collapses in the other.
    """
    n = len(vols)
    if n < 60:
        return {"ok": False, "reason": f"only {n} observations"}
    h = n // 2
    a = persistence(vols[:h], lag)
    b = persistence(vols[h:], lag)
    consistent = (a["r"] > 0) == (b["r"] > 0) and min(a["r"], b["r"]) > 0.05
    return {"ok": True, "first": a, "second": b, "consistent": consistent}


def horizon_days(min_duration: str | None) -> float:
    """Deriv duration string ('1d', '7d', '15m') to days.

    The forecast has to be made at the horizon the contract settles over.
    Measuring day-ahead persistence to justify a seven-day contract is the
    same category of error as measuring it at the wrong block size.
    """
    if not min_duration:
        return float("inf")
    unit, num = min_duration[-1], min_duration[:-1]
    try:
        n = float(num)
    except ValueError:
        return float("inf")
    return {"t": n / 86400, "s": n / 86400, "m": n / 1440,
            "h": n / 24, "d": n}.get(unit, float("inf"))


def block_vols(vols: Sequence[float], block: int) -> list[float]:
    """Average volatility over non-overlapping blocks of `block` periods.

    Non-overlapping on purpose. Overlapping windows share data between
    consecutive points, which manufactures autocorrelation from nothing - the
    single easiest way to produce a persistence result that is not there.
    """
    if block <= 1:
        return list(vols)
    out = []
    for i in range(0, len(vols) - block + 1, block):
        out.append(statistics.fmean(vols[i:i + block]))
    return out


def forecast_value(r: float) -> dict[str, Any]:
    """Turn a persistence correlation into the thing that decides it: money.

    `r` is the correlation between today's log volatility and tomorrow's, so
    r^2 is the fraction of tomorrow's variation the forecast explains. That is
    an upper bound on the edge - it assumes the forecast is converted into a
    position perfectly and that the market prices at a flat volatility, both
    generous. Compared against the margin actually charged, it says whether
    there is anything left after costs.
    """
    explained = r * r
    return {"r": r, "variance_explained": explained,
            "explained_pct": explained * 100.0}
