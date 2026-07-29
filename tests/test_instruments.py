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
    assert build_proposal(up(), TOUCH, "R_10", 3.0)["barrier"].startswith("+")
    assert build_proposal(down(), TOUCH, "R_10", 3.0)["barrier"].startswith("-")
    assert build_proposal(up(), TOUCH, "R_10", 3.0)["contract_type"] == "ONETOUCH"


def test_no_touch_expresses_a_forecast_of_no_move():
    """The only one of the three that can hold a position on 'nothing
    happens' rather than expressing it as inaction."""
    flat = Signal(0, FLAT_MOVE_PCT / 2, 900, 0.8, "quiet")
    p = build_proposal(flat, TOUCH, "R_10", 3.0)
    assert p is not None
    assert p["contract_type"] == "NOTOUCH"


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
