import pytest

from deriv_bot.ladder_risk import (
    ladder_cost,
    max_base_for_risk,
    summarise,
    wipeout_every_n_trades,
)


def test_ladder_cost_is_the_geometric_sum():
    # 5+10+20+40+80+160+320
    assert ladder_cost(5, 7) == 635
    assert ladder_cost(1, 7) == 127
    assert ladder_cost(5, 1) == 5


def test_max_base_scales_with_capital_and_risk():
    # the user's own principle: match the matrix to the capital
    assert max_base_for_risk(2795.13, 7, 0.05) == pytest.approx(1.10, abs=0.01)
    assert max_base_for_risk(2795.13, 7, 0.10) == pytest.approx(2.20, abs=0.01)
    # and it round-trips against ladder_cost
    base = max_base_for_risk(1000, 7, 0.10)
    assert ladder_cost(base, 7) == pytest.approx(100.0)


def test_max_base_handles_degenerate_input():
    assert max_base_for_risk(0, 7, 0.1) == 0.0
    assert max_base_for_risk(1000, 0, 0.1) == 0.0


def test_wipeout_frequency_matches_the_simulated_figure():
    # 400k simulated trades gave one wipeout per ~256-258 trades
    assert wipeout_every_n_trades(7, 0.5) == pytest.approx(256, rel=0.02)


def test_deeper_ladders_wipe_out_less_often():
    assert wipeout_every_n_trades(8, 0.5) > wipeout_every_n_trades(7, 0.5)


def test_summarise_translates_rarity_per_cycle_into_per_day():
    """The point of the module: 1-in-128 per cycle is not rare once the bot
    runs unattended - it becomes several wipeouts a day."""
    s = summarise(capital=2795.13, base=5.0, rungs=7,
                  win_prob=0.5, seconds_per_trade=45.0)
    assert s["ladder_cost"] == 635
    assert s["pct_of_capital"] == pytest.approx(22.7, abs=0.1)
    assert s["hours_between_wipeouts"] == pytest.approx(3.2, abs=0.1)
    assert s["wipeouts_per_day"] == pytest.approx(7.5, abs=0.2)
    assert s["ladders_capital_covers"] == pytest.approx(4.4, abs=0.1)


def test_smaller_base_reduces_both_cost_and_share():
    small = summarise(2795.13, 1.10, 7)
    assert small["pct_of_capital"] == pytest.approx(5.0, abs=0.1)
    # frequency is unchanged by stake size - only the damage per event moves
    assert small["wipeouts_per_day"] == pytest.approx(
        summarise(2795.13, 5.0, 7)["wipeouts_per_day"])
