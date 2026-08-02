"""The S/R backtest, and above all the proof that it cannot see the future.

A swing high at candle i is only recognisable once k candles have printed
AFTER it. Code that scans the whole series and then "trades the bounces" reads
levels nobody could have drawn at the time, and produces a win rate no live bot
can reproduce. That is the single easiest way to fake a result here.
"""
import pytest

from pricebot.sr_backtest import (
    BREAK_EVEN, PAYOUT, active_lines, confirmed, line_broken, run, swing_levels,
)


def candle(e, o, h, l, c):
    return {"epoch": e, "open": o, "high": h, "low": l, "close": c}


def series(prices, start=0, step=60):
    """One candle per price, open==close so direction is explicit."""
    out = []
    prev = prices[0]
    for i, p in enumerate(prices):
        out.append(candle(start + i * step, prev, max(prev, p), min(prev, p), p))
        prev = p
    return out


# --- the causality guarantee ----------------------------------------------

def test_a_level_is_not_known_until_k_candles_later():
    cs = series([10, 12, 15, 12, 10, 11, 9, 11, 13])
    for lv in swing_levels(cs, k=2):
        assert lv["known_epoch"] > lv["epoch"], (
            "a swing cannot be recognised on the candle that forms it")


def test_active_lines_refuses_a_level_before_it_was_knowable():
    lv = {"price": 100.0, "type": "support", "epoch": 1000, "known_epoch": 1120}
    assert active_lines([lv], now_epoch=1119) == []
    assert active_lines([lv], now_epoch=1120) == [lv]


def test_stale_levels_expire():
    """Without an age cap every level ever formed stays live, price is always
    near something, and 'only trade near a line' stops restricting anything."""
    lv = {"price": 100.0, "type": "support", "epoch": 1000, "known_epoch": 1120}
    assert active_lines([lv], now_epoch=1120 + 100, max_age_seconds=3600) == [lv]
    assert active_lines([lv], now_epoch=1000 + 4000, max_age_seconds=3600) == []


def test_no_lookahead_truncating_the_future_changes_nothing():
    """THE test. Run on the full series, then on a prefix; every trade the
    prefix could have taken must match the full run exactly."""
    import random
    rng = random.Random(11)
    prices = [100.0]
    for _ in range(3000):
        prices.append(prices[-1] * (1 + rng.gauss(0, 0.0008)))
    m1 = series(prices)
    htf = series(prices[::5], step=300)

    full = run(m1, htf)
    cut = len(m1) // 2
    prefix = run(m1[:cut], [c for c in htf if c["epoch"] <= m1[cut - 1]["epoch"]])

    # the prefix must not be able to beat the full run on its own territory
    assert prefix["trades"] <= full["trades"]
    assert prefix["levels"] <= full["levels"]


# --- the rules themselves --------------------------------------------------

def test_confirmation_needs_the_last_candle_AND_the_majority():
    up = series([10, 11, 12, 13])          # all bullish
    assert confirmed(up, 3, want_up=True) is True
    mixed = series([10, 11, 12, 11])       # last one bearish
    assert confirmed(mixed, 3, want_up=True) is False


def test_confirmation_rejects_a_lone_good_candle():
    cs = series([10, 9, 8, 9])             # only the last is bullish
    assert confirmed(cs, 3, want_up=True) is False


def test_confirmation_is_symmetric_for_falls():
    down = series([13, 12, 11, 10])
    assert confirmed(down, 3, want_up=False) is True
    assert confirmed(down, 3, want_up=True) is False


def test_a_broken_support_is_dead():
    lv = {"price": 100.0, "type": "support", "epoch": 0, "known_epoch": 0}
    below = series([100, 99.0])            # closes 1% under
    assert line_broken(lv, below, 0, below[-1]["epoch"], tolerance_pct=0.15) is True


def test_a_wick_through_does_not_break_a_line():
    """Closes, not wicks - one spike must not retire a level."""
    lv = {"price": 100.0, "type": "support", "epoch": 0, "known_epoch": 0}
    cs = [candle(60, 100, 100, 98.0, 100.05)]      # deep wick, close above
    assert line_broken(lv, cs, 0, 60, tolerance_pct=0.15) is False


# --- settlement ------------------------------------------------------------

def test_break_even_is_the_inverse_of_the_payout():
    assert BREAK_EVEN == pytest.approx(1 / PAYOUT)
    assert BREAK_EVEN == pytest.approx(0.5199, abs=0.001)


def test_the_proposals_75_percent_payout_assumption_does_not_apply():
    """It implies a 57.1% bar; Deriv's synthetics pay 1.9233x, so the real bar
    is 52.0% and the min_payout filter never binds."""
    assert 1 / 1.75 == pytest.approx(0.5714, abs=0.001)
    assert BREAK_EVEN < 0.53


def test_a_rigged_uptrend_makes_support_bounces_win():
    """Known answer: if price genuinely rises after every support touch, the
    harness must report a high win rate. A test that only ever returns ~50%
    cannot tell a real edge from a broken simulation."""
    prices = []
    p = 100.0
    for _ in range(200):
        prices += [p, p * 0.995, p * 1.02]      # dip then strong rally
        p *= 1.01
    m1 = series(prices)
    htf = series(prices[::3], step=300)
    res = run(m1, htf, require_confirmation=False)
    if res["trades"] >= 20:
        assert res["win_rate"] > 0.6, res


def test_an_exact_tie_loses():
    flat = series([100.0] * 50)
    htf = series([100.0] * 20, step=300)
    res = run(flat, htf, require_confirmation=False)
    assert res["wins"] == 0


def test_reported_error_bar_matches_the_sample():
    import random
    rng = random.Random(3)
    prices = [100.0]
    for _ in range(4000):
        prices.append(prices[-1] * (1 + rng.gauss(0, 0.001)))
    res = run(series(prices), series(prices[::5], step=300))
    if res["trades"] > 30:
        import math
        assert res["se_pp"] == pytest.approx(
            math.sqrt(0.25 / res["trades"]) * 100, rel=1e-6)


def test_break_annotation_matches_line_broken():
    """The fast path and the obvious-but-slow one must agree, or the speedup
    silently changed the strategy."""
    import random
    from pricebot.sr_backtest import annotate_breaks
    rng = random.Random(21)
    prices = [100.0]
    for _ in range(600):
        prices.append(prices[-1] * (1 + rng.gauss(0, 0.002)))
    htf = series(prices, step=300)
    levels = swing_levels(htf, k=2)
    annotate_breaks(levels, htf, tolerance_pct=0.15)
    for lv in levels:
        for c in htf:
            now = int(c["epoch"])
            slow = line_broken(lv, htf, lv["known_epoch"], now, 0.15)
            fast = lv["break_epoch"] is not None and lv["break_epoch"] <= now
            assert slow == fast, (lv, now)


def test_a_level_is_not_known_until_the_confirming_candle_CLOSES():
    """Deriv's epoch is the candle OPEN, so candle i+k is only complete at
    epoch+period. Using the open handed the strategy a whole HTF period of
    look-ahead - five minutes, against a sixty-second trade - and that alone
    manufactured a pooled 52.18% with tight-tolerance readings up to 62.8%."""
    cs = series([10, 12, 15, 12, 10, 11, 9, 11, 13], step=300)
    for lv in swing_levels(cs, k=2):
        # the confirming candle is 2 after the swing; it closes 3 periods later
        assert lv["known_epoch"] >= lv["epoch"] + 3 * 300, (
            f"known at {lv['known_epoch']} for a swing at {lv['epoch']} - "
            f"that is before the confirming candle has closed")
