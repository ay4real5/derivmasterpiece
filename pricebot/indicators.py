"""EMA, RSI, Bollinger Bands, ADX and candle patterns.

Standard indicators, implemented from their definitions rather than pulled
from a library, so every value the scoring engine sees can be traced and
checked. Each is easy to get subtly wrong in a way no log would reveal:

- EMA seeded with a simple average of the first `period` values, not with
  the first value alone. Seeding with a single point makes early output
  drift for roughly 3x the period, which on a 50-candle buffer is most of
  the series.
- RSI and ADX use WILDER smoothing (alpha = 1/period), not the exponential
  alpha = 2/(period+1) used for EMAs. Mixing the two is the commonest RSI
  bug and produces values that look plausible while being consistently wrong.
- ADX needs two rounds of smoothing - once for the directional movements and
  again for DX itself - so it needs about 2x period of history before it
  means anything.

Every function returns the full series aligned to the input, with None where
there is not yet enough history, so a caller can never silently read an
unwarmed value as a real one.
"""
from __future__ import annotations

import statistics
from typing import Any, Sequence


def _closes(candles: Sequence[dict[str, Any]]) -> list[float]:
    return [float(c["close"]) for c in candles]


def sma(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """Exponential moving average, seeded with the SMA of the first `period`."""
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * alpha + prev * (1 - alpha)
        out[i] = prev
    return out


def rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI. Note alpha = 1/period, NOT the EMA's 2/(period+1)."""
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = [], []
    for a, b in zip(values, values[1:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi(g: float, l: float) -> float:
        if l == 0:
            return 100.0
        rs = g / l
        return 100.0 - (100.0 / (1.0 + rs))

    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi(avg_gain, avg_loss)
    return out


def bollinger(values: Sequence[float], period: int = 20,
              num_std: float = 2.0) -> tuple[list[float | None], list[float | None],
                                             list[float | None]]:
    """(lower, middle, upper). Population stdev, matching the usual convention."""
    mid = sma(values, period)
    lower: list[float | None] = [None] * len(values)
    upper: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if mid[i] is None:
            continue
        window = values[i - period + 1: i + 1]
        sd = statistics.pstdev(window)
        lower[i] = mid[i] - num_std * sd
        upper[i] = mid[i] + num_std * sd
    return lower, mid, upper


def adx(candles: Sequence[dict[str, Any]], period: int = 14) -> list[float | None]:
    """Wilder's ADX - trend STRENGTH, direction-blind.

    Two smoothing passes: directional movement and true range first, then DX
    into ADX. So the first meaningful value needs roughly 2 x period of
    history, which is why the PDF's 50-candle buffer is about the minimum
    for a 14-period ADX to be trustworthy.
    """
    n = len(candles)
    out: list[float | None] = [None] * n
    if n < period * 2 + 1:
        return out

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]

    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(highs[i] - lows[i],
                      abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))

    # Wilder smoothing of the three series
    sm_plus = sum(plus_dm[:period])
    sm_minus = sum(minus_dm[:period])
    sm_tr = sum(tr[:period])
    dx_series: list[tuple[int, float]] = []
    for i in range(period, len(tr)):
        sm_plus = sm_plus - sm_plus / period + plus_dm[i]
        sm_minus = sm_minus - sm_minus / period + minus_dm[i]
        sm_tr = sm_tr - sm_tr / period + tr[i]
        if sm_tr == 0:
            continue
        pdi = 100.0 * sm_plus / sm_tr
        mdi = 100.0 * sm_minus / sm_tr
        denom = pdi + mdi
        if denom == 0:
            continue
        dx_series.append((i + 1, 100.0 * abs(pdi - mdi) / denom))

    if len(dx_series) < period:
        return out
    # second pass: smooth DX into ADX
    first = sum(v for _, v in dx_series[:period]) / period
    idx, _ = dx_series[period - 1]
    out[idx] = first
    prev = first
    for k in range(period, len(dx_series)):
        idx, dxv = dx_series[k]
        prev = (prev * (period - 1) + dxv) / period
        out[idx] = prev
    return out


def candle_pattern(candles: Sequence[dict[str, Any]]) -> str:
    """Momentum from the last three closed candles.

    Returns 'bullish' | 'bearish' | 'partial_bull' | 'partial_bear' |
    'neutral', following the PDF's table: three in a row is momentum, two
    plus a doji is partial, a doji or alternating candles is no signal.
    """
    if len(candles) < 3:
        return "neutral"
    last3 = candles[-3:]

    def body_ratio(c: dict[str, Any]) -> float:
        hi, lo = float(c["high"]), float(c["low"])
        rng = hi - lo
        if rng <= 0:
            return 0.0
        return abs(float(c["close"]) - float(c["open"])) / rng

    # A doji is indecision, not a direction, even when its close sits a hair
    # above its open. Counting it as bullish turns "2 up and a doji" - which
    # the spec calls PARTIAL - into a full three-candle signal.
    is_doji = [body_ratio(c) < 0.10 for c in last3]
    dojis = sum(is_doji)
    ups = sum(1 for c, d in zip(last3, is_doji)
              if not d and float(c["close"]) > float(c["open"]))
    downs = sum(1 for c, d in zip(last3, is_doji)
                if not d and float(c["close"]) < float(c["open"]))

    rising = all(float(b["close"]) > float(a["close"])
                 for a, b in zip(last3, last3[1:]))
    falling = all(float(b["close"]) < float(a["close"])
                  for a, b in zip(last3, last3[1:]))

    if ups == 3 and rising:
        return "bullish"
    if downs == 3 and falling:
        return "bearish"
    if dojis >= 1 and ups == 2:
        return "partial_bull"
    if dojis >= 1 and downs == 2:
        return "partial_bear"
    return "neutral"
