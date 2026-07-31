import pytest

from pricebot.signals import (
    FixedNoTouch,
    MeanReversion,
    Momentum,
    NeverTrade,
    Signal,
    build_strategy,
)


def candles(closes, start=100.0):
    out, price = [], start
    for c in closes:
        out.append({"open": price, "high": max(price, c), "low": min(price, c), "close": c})
        price = c
    return out


# --- the Signal contract ------------------------------------------------

def test_a_signal_needs_direction_move_and_horizon():
    s = Signal(1, 0.002, 900, 0.5, "why")
    assert s.actionable


def test_a_flat_signal_is_not_actionable():
    # returning zeros is how a strategy looks busy while doing nothing
    assert not Signal(0, 0.002, 900, 0.5, "no view").actionable
    assert not Signal(1, 0.0, 900, 0.5, "no move").actionable


def test_signal_rejects_impossible_values():
    for bad in (
        dict(direction=2, expected_move_pct=0.01, horizon_seconds=60, confidence=0.5),
        dict(direction=1, expected_move_pct=-0.01, horizon_seconds=60, confidence=0.5),
        dict(direction=1, expected_move_pct=0.01, horizon_seconds=0, confidence=0.5),
        dict(direction=1, expected_move_pct=0.01, horizon_seconds=60, confidence=1.5),
    ):
        with pytest.raises(ValueError):
            Signal(reason="x", **bad)


# --- the baseline that matters -----------------------------------------

def test_never_trades_nothing_ever():
    """The default, and the bar every strategy must clear. The digit bot
    measured cost per bet for three days and never compared against not
    betting at all."""
    s = NeverTrade()
    assert s.evaluate(candles([100, 105, 110, 120])) is None
    assert s.evaluate([]) is None


# --- momentum / mean reversion -----------------------------------------

def test_momentum_needs_enough_candles():
    assert Momentum(lookback=20).evaluate(candles([100, 101])) is None


def test_momentum_ignores_a_move_below_its_threshold():
    m = Momentum(lookback=3, min_move_pct=0.05)
    assert m.evaluate(candles([100, 100.5, 101])) is None


def test_momentum_goes_long_on_a_rise():
    m = Momentum(lookback=3, min_move_pct=0.001)
    sig = m.evaluate(candles([100, 105, 110]))
    assert sig.direction == 1
    assert sig.expected_move_pct > 0
    assert "momentum" in sig.reason


def test_momentum_goes_short_on_a_fall():
    m = Momentum(lookback=3, min_move_pct=0.001)
    assert m.evaluate(candles([100, 95, 90])).direction == -1


def test_mean_reversion_is_exactly_inverted():
    data = candles([100, 105, 110])
    mo = Momentum(lookback=3, min_move_pct=0.001).evaluate(data)
    mr = MeanReversion(lookback=3, min_move_pct=0.001).evaluate(data)
    assert mr.direction == -mo.direction
    assert mr.expected_move_pct == mo.expected_move_pct


def test_confidence_is_capped_at_one():
    m = Momentum(lookback=3, min_move_pct=0.001)
    sig = m.evaluate(candles([100, 200, 400]))   # enormous move
    assert sig.confidence == 1.0


def test_registry_and_bad_names():
    assert isinstance(build_strategy("never"), NeverTrade)
    assert isinstance(build_strategy("momentum", lookback=5), Momentum)
    with pytest.raises(ValueError):
        build_strategy("does_not_exist")


def test_momentum_rejects_bad_parameters():
    with pytest.raises(ValueError):
        Momentum(lookback=1)
    with pytest.raises(ValueError):
        Momentum(min_move_pct=0)


# --- fixed_notouch -------------------------------------------------------

def test_fixed_notouch_always_fires_the_same_shape_regardless_of_candles():
    """No forecast: it doesn't look at the candles at all, unlike every
    other strategy here."""
    s = FixedNoTouch(barrier_pct=0.30, horizon_seconds=300)
    for data in ([], candles([100, 105, 110]), candles([100, 90, 80, 200])):
        sig = s.evaluate(data)
        assert sig.direction == 0
        assert sig.expected_move_pct == 0.30
        assert sig.horizon_seconds == 300


def test_fixed_notouch_is_not_actionable_but_is_a_deliberate_no_view():
    """direction == 0 means Signal.actionable is False - this is what tells
    build_proposal/runner.py it is a no-view TOUCH bet, not a dead signal."""
    sig = FixedNoTouch().evaluate([])
    assert not sig.actionable
    assert sig.direction == 0 and sig.expected_move_pct > 0


def test_fixed_notouch_rejects_bad_parameters():
    with pytest.raises(ValueError):
        FixedNoTouch(barrier_pct=0)
    with pytest.raises(ValueError):
        FixedNoTouch(horizon_seconds=0)
    with pytest.raises(ValueError):
        FixedNoTouch(barrier_by_symbol={"R_75": -0.01})


def test_fixed_notouch_per_symbol_override():
    """The same percentage barrier does not buy the same win-rate shape on
    every symbol (confirmed by scan-touch: R_50 needed 0.30%, R_75 needed
    0.40%, for a comparable ~93-96% win rate at 5 minutes) - a symbol not
    listed falls back to the plain barrier_pct."""
    s = FixedNoTouch(barrier_pct=0.003, barrier_by_symbol={"R_75": 0.004, "R_100": 0.005})
    assert s.evaluate([], symbol="R_50").expected_move_pct == 0.003   # not overridden
    assert s.evaluate([], symbol="R_75").expected_move_pct == 0.004
    assert s.evaluate([], symbol="R_100").expected_move_pct == 0.005
    assert s.evaluate([]).expected_move_pct == 0.003                  # no symbol at all


def test_fixed_notouch_reason_names_the_symbol():
    sig = FixedNoTouch(barrier_pct=0.003).evaluate([], symbol="R_50")
    assert "R_50" in sig.reason


def test_fixed_notouch_is_registered():
    s = build_strategy("fixed_notouch", barrier_pct=0.2, horizon_seconds=120)
    assert isinstance(s, FixedNoTouch)
    assert s.barrier_pct == 0.2
