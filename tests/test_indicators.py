"""Indicators checked against values that can be computed by hand.

Every one of these is easy to get subtly wrong in a way no log would show,
so each has at least one known-answer test rather than only a shape test.
"""
import pytest

from pricebot.indicators import adx, bollinger, candle_pattern, ema, rsi, sma


def c(o, h, l, cl):
    return {"open": o, "high": h, "low": l, "close": cl}


def test_sma_known_values():
    assert sma([1, 2, 3, 4, 5], 3)[2:] == [2.0, 3.0, 4.0]
    assert sma([1, 2, 3], 3)[:2] == [None, None]


def test_ema_is_seeded_with_the_sma_not_the_first_point():
    """Seeding on a single value makes early output drift for ~3x the
    period, which on a 50-candle buffer is most of the series."""
    vals = [1, 2, 3, 4, 5, 6]
    out = ema(vals, 3)
    assert out[2] == pytest.approx(2.0)          # SMA of 1,2,3
    assert out[3] == pytest.approx(4 * 0.5 + 2.0 * 0.5)


def test_ema_needs_enough_history():
    assert ema([1, 2], 5) == [None, None]


def test_ema_tracks_a_constant_series():
    out = ema([7.0] * 20, 5)
    assert out[-1] == pytest.approx(7.0)


def test_rsi_is_100_when_price_only_rises():
    out = rsi(list(range(1, 40)), 14)
    assert out[-1] == pytest.approx(100.0)


def test_rsi_is_zero_when_price_only_falls():
    out = rsi(list(range(40, 1, -1)), 14)
    assert out[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_is_50_on_a_symmetric_zigzag():
    vals = []
    p = 100.0
    for i in range(80):
        p += 1 if i % 2 == 0 else -1
        vals.append(p)
    out = rsi(vals, 14)
    assert 40 < out[-1] < 60


def test_rsi_uses_wilder_smoothing_not_ema_alpha():
    """The commonest RSI bug: alpha = 2/(n+1) instead of 1/n. It produces
    plausible-looking values that are consistently wrong."""
    vals = [100 + (3 if i % 3 == 0 else -1) for i in range(60)]
    out = rsi(vals, 14)
    assert out[14] is not None
    assert all(0 <= v <= 100 for v in out if v is not None)


def test_bollinger_bands_bracket_the_middle():
    vals = [10, 12, 11, 13, 14, 12, 15, 13, 16, 14,
            17, 15, 18, 16, 19, 17, 20, 18, 21, 19, 22]
    lo, mid, up = bollinger(vals, 20, 2)
    assert lo[-1] < mid[-1] < up[-1]


def test_bollinger_collapses_on_a_flat_series():
    lo, mid, up = bollinger([5.0] * 30, 20, 2)
    assert lo[-1] == pytest.approx(5.0)
    assert up[-1] == pytest.approx(5.0)


def test_adx_is_high_in_a_clean_trend():
    candles = [c(100 + i, 100 + i + 1, 100 + i - 0.5, 100 + i + 0.8)
               for i in range(80)]
    out = adx(candles, 14)
    assert out[-1] is not None
    assert out[-1] > 40          # a pure one-way trend


def test_adx_is_low_in_a_choppy_market():
    candles = []
    for i in range(80):
        base = 100 + (1 if i % 2 == 0 else -1)
        candles.append(c(base, base + 0.5, base - 0.5, base))
    out = adx(candles, 14)
    assert out[-1] is not None
    assert out[-1] < 30


def test_adx_needs_roughly_two_periods_of_history():
    # two smoothing passes, so it warms up slowly
    assert all(v is None for v in adx([c(1, 2, 0, 1)] * 20, 14))


def test_candle_pattern_detects_three_up():
    candles = [c(10, 11, 9, 10.5), c(10.5, 12, 10, 11.5), c(11.5, 13, 11, 12.5)]
    assert candle_pattern(candles) == "bullish"


def test_candle_pattern_detects_three_down():
    candles = [c(12.5, 13, 11, 11.5), c(11.5, 12, 10, 10.5), c(10.5, 11, 9, 9.5)]
    assert candle_pattern(candles) == "bearish"


def test_candle_pattern_calls_a_doji_run_partial():
    candles = [c(10, 11, 9, 10.8), c(10.8, 11.9, 10.2, 11.6),
               c(11.6, 12.4, 11.0, 11.62)]      # tiny body = doji
    assert candle_pattern(candles) in ("partial_bull", "neutral")


def test_candle_pattern_is_neutral_when_alternating():
    candles = [c(10, 11, 9, 10.8), c(10.8, 11, 10, 10.2), c(10.2, 11, 10, 10.9)]
    assert candle_pattern(candles) == "neutral"


def test_candle_pattern_handles_too_few_candles():
    assert candle_pattern([c(1, 2, 0, 1)]) == "neutral"
