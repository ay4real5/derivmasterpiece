import pytest

from pricebot.instruments import (
    DEFAULT_STOP_FRACTION,
    FLAT_MOVE_PCT,
    MULTIPLIER,
    RISE_FALL,
    TOUCH,
    VALID_MULTIPLIERS,
    build_proposal,
    stake_for,
)
from pricebot.signals import Signal


def up(move=0.002, horizon=900, conf=1.0):
    return Signal(1, move, horizon, conf, "test")


def down(move=0.002, horizon=900, conf=1.0):
    return Signal(-1, move, horizon, conf, "test")


# --- multipliers --------------------------------------------------------

def test_multiplier_direction_maps_to_the_right_contract():
    assert build_proposal(up(), MULTIPLIER, "R_10", 3.0)["contract_type"] == "MULTUP"
    assert build_proposal(down(), MULTIPLIER, "R_10", 3.0)["contract_type"] == "MULTDOWN"


def test_multiplier_uses_only_leverage_deriv_accepts():
    # the API rejects anything else; found from its own error message
    for move in (0.0001, 0.001, 0.01, 0.5):
        p = build_proposal(Signal(1, move, 900, 1.0, "x"), MULTIPLIER, "R_10", 3.0)
        assert p["multiplier"] in VALID_MULTIPLIERS


def test_stop_loss_sits_strictly_inside_the_stake():
    """The trap this exists to prevent: Deriv stops a multiplier out at 100%
    of stake regardless, so a stop-loss at or beyond the stake never fires
    and the position silently runs to full loss instead."""
    for move in (0.0001, 0.001, 0.01, 0.1, 0.9):
        stake = 3.0
        p = build_proposal(Signal(1, move, 900, 1.0, "x"), MULTIPLIER, "R_10", stake)
        assert p["limit_order"]["stop_loss"] < stake


def test_stop_loss_is_smaller_than_take_profit():
    p = build_proposal(up(), MULTIPLIER, "R_10", 3.0)
    lo = p["limit_order"]
    assert lo["stop_loss"] < lo["take_profit"]
    assert lo["stop_loss"] == pytest.approx(lo["take_profit"] * DEFAULT_STOP_FRACTION,
                                            abs=0.02)


def test_both_orders_are_always_positive():
    p = build_proposal(Signal(1, 1e-6, 900, 1.0, "tiny"), MULTIPLIER, "R_10", 3.0)
    assert p["limit_order"]["take_profit"] > 0
    assert p["limit_order"]["stop_loss"] > 0


def test_stop_fraction_must_be_a_fraction():
    with pytest.raises(ValueError):
        build_proposal(up(), MULTIPLIER, "R_10", 3.0, stop_fraction=1.0)


# --- rise/fall ----------------------------------------------------------

def test_rise_fall_maps_direction_and_horizon():
    p = build_proposal(up(horizon=1800), RISE_FALL, "R_10", 3.0)
    assert p["contract_type"] == "CALL"
    assert p["duration"] == 30 and p["duration_unit"] == "m"
    assert build_proposal(down(), RISE_FALL, "R_10", 3.0)["contract_type"] == "PUT"


def test_rise_fall_duration_never_rounds_to_zero():
    assert build_proposal(up(horizon=10), RISE_FALL, "R_10", 3.0)["duration"] >= 1


# --- touch / no touch ---------------------------------------------------

def test_touch_expects_a_move_in_the_signalled_direction():
    assert build_proposal(up(), TOUCH, "R_10", 3.0, spot=100.0)["barrier"].startswith("+")
    assert build_proposal(down(), TOUCH, "R_10", 3.0, spot=100.0)["barrier"].startswith("-")
    assert build_proposal(up(), TOUCH, "R_10", 3.0, spot=100.0)["contract_type"] == "ONETOUCH"


def test_touch_requires_a_spot_price():
    """Deriv's relative barrier is an ABSOLUTE offset from spot, not a
    fraction of it - confirmed live: the same nominal offset means a 0.27%
    barrier on a ~112-priced symbol and a 0.00004% barrier on a
    ~780,000-priced one. A fraction can't become that offset without the
    current price to scale it by."""
    with pytest.raises(ValueError):
        build_proposal(up(), TOUCH, "R_10", 3.0)


def test_touch_barrier_scales_the_fraction_by_spot():
    """expected_move_pct=0.002 (0.2%) on a spot of 100 must produce a 0.20
    absolute offset, not the raw 0.002 - the bug this fix corrects."""
    p = build_proposal(up(move=0.002), TOUCH, "R_10", 3.0, spot=100.0)
    assert p["barrier"] == "+0.2000"


def test_touch_barrier_is_symbol_price_aware():
    """The same fraction must produce a different absolute barrier on a
    different-priced symbol - this is the whole point of the fix."""
    cheap = build_proposal(up(move=0.01), TOUCH, "R_10", 3.0, spot=100.0)
    expensive = build_proposal(up(move=0.01), TOUCH, "R_75", 3.0, spot=50000.0)
    assert cheap["barrier"] == "+1.0000"
    assert expensive["barrier"] == "+500.0000"


def test_no_touch_expresses_a_forecast_of_no_move():
    """The only one of the three that can hold a position on 'nothing
    happens' rather than expressing it as inaction."""
    flat = Signal(0, FLAT_MOVE_PCT / 2, 900, 0.8, "quiet")
    p = build_proposal(flat, TOUCH, "R_10", 3.0, spot=100.0)
    assert p is not None
    assert p["contract_type"] == "NOTOUCH"


def test_no_touch_also_expresses_a_no_view_wide_barrier():
    """Distinct from the 'quiet market' case above: direction == 0 with a
    deliberately WIDE move is 'no forecast, buy this win-rate shape' (see
    deriv_bot/touch_edge.py) - not a claim the price will stay put."""
    wide = Signal(0, 0.30, 300, 1.0, "fixed cheap barrier")
    p = build_proposal(wide, TOUCH, "R_50", 3.0, spot=100.0)
    assert p is not None
    assert p["contract_type"] == "NOTOUCH"
    assert p["barrier"] == "+30.0000"
    assert len(p["barrier"].split(".")[1]) <= 4, "Deriv rejects a 5th decimal place"


def test_directional_signals_are_unaffected_by_the_no_view_case():
    """direction != 0 still always means ONETOUCH, regardless of move size -
    the new no_view branch must not swallow directional signals."""
    assert build_proposal(up(move=0.30), TOUCH, "R_50", 3.0, spot=100.0)["contract_type"] == "ONETOUCH"
    assert build_proposal(down(move=0.30), TOUCH, "R_50", 3.0, spot=100.0)["contract_type"] == "ONETOUCH"


# --- no trade is a first-class outcome ----------------------------------

def test_a_signal_with_no_direction_produces_no_trade():
    flat = Signal(0, 0.002, 900, 0.9, "no view")
    assert build_proposal(flat, MULTIPLIER, "R_10", 3.0) is None
    assert build_proposal(flat, RISE_FALL, "R_10", 3.0) is None


def test_unknown_instrument_and_bad_stake_are_refused():
    with pytest.raises(ValueError):
        build_proposal(up(), "options", "R_10", 3.0)
    with pytest.raises(ValueError):
        build_proposal(up(), MULTIPLIER, "R_10", 0)


# --- sizing -------------------------------------------------------------

def test_stake_scales_with_confidence_not_with_losses():
    # deliberately not a ladder: the digit bot's losses exceeded its own
    # house edge because doubling pushed volume into losing runs
    assert stake_for(up(conf=1.0), 10.0) == 10.0
    assert stake_for(up(conf=0.5), 10.0) == 5.0
    assert stake_for(up(conf=0.0), 10.0) == 0.0


def test_stake_respects_a_cap():
    assert stake_for(up(conf=1.0), 10.0, max_stake=4.0) == 4.0


# --- per-symbol multiplier ranges ---------------------------------------

def test_leverage_uses_the_symbols_own_range():
    """Deriv's ranges are per-instrument and unlike each other. A hardcoded
    tuple fitted only R_10: gold was rejected on every attempt of a live
    session and never traded once, while R_25 worked by coincidence."""
    from pricebot.instruments import _leverage
    gold = (100, 200, 300, 500, 800)
    m = _leverage(up(move=0.0008), stake=50.0, allowed=gold, commission=0.60)
    assert m in gold


def test_leverage_prefers_the_smallest_that_clears_the_fee():
    # cost per day scales with multiplier SQUARED, so bigger is not better -
    # it only climbs far enough for the take-profit to beat the commission
    from pricebot.instruments import _leverage
    allowed = (100, 200, 300, 500, 800)
    cheap = _leverage(up(move=0.01), stake=50.0, allowed=allowed, commission=0.60)
    dear = _leverage(up(move=0.00001), stake=50.0, allowed=allowed, commission=0.60)
    assert cheap == 100          # a big move needs no leverage to pay
    assert dear > cheap          # a tiny one has to climb to beat the fee


def test_build_proposal_honours_the_allowed_range():
    gold = (100, 200, 300, 500, 800)
    p = build_proposal(up(move=0.0008), MULTIPLIER, "frxXAUUSD", 50.0,
                       allowed_multipliers=gold, commission=0.60)
    assert p["multiplier"] in gold


def test_take_profit_matches_the_multiplier_actually_used():
    gold = (100, 200, 300, 500, 800)
    p = build_proposal(up(move=0.001), MULTIPLIER, "frxXAUUSD", 50.0,
                       allowed_multipliers=gold, commission=0.60)
    expected = round(50.0 * 0.001 * p["multiplier"], 2)
    assert p["limit_order"]["take_profit"] == pytest.approx(expected)


def test_an_empty_range_is_refused_not_guessed():
    from pricebot.instruments import _leverage
    with pytest.raises(ValueError):
        _leverage(up(), stake=50.0, allowed=(), commission=0.6)
