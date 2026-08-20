"""The simulator has to be trustworthy before its output means anything, so
these check it against cases where the answer is known independently: a
zero-margin game must come out flat, a real observed blowup must reproduce,
and a fixed seed must be deterministic."""
import pytest

from deriv_bot.group_ladder import GROUPS, GroupLadder, GroupSpec
from tools.ladder_lab import RISE_FALL_PAYOUT, ladder_cost, simulate


def test_zero_margin_game_is_break_even_on_average():
    """At payout 2.0 and win 50% the game is exactly fair, so a large sample
    must land near the starting bankroll. If this drifts, the simulator is
    inventing or destroying money somewhere."""
    r = simulate(payout=2.0, win_prob=0.5, trials=1500, days=10,
                 trades_per_day=40, bankroll=10_000.0,
                 recovery_mode="breakeven", on_exhaust="reset",
                 max_daily_loss=None, target_profit=None)
    assert 9_500 < r["mean_final"] < 10_500


def test_house_margin_shows_up_as_a_loss():
    """The real contract is NOT fair - 1.9233 payout at 48% wins. The sim must
    reproduce a loss, otherwise it is not modelling the margin at all."""
    r = simulate(payout=RISE_FALL_PAYOUT, win_prob=0.48, trials=1000, days=20,
                 trades_per_day=40, recovery_mode="breakeven",
                 on_exhaust="reset")
    assert r["mean_final"] < 10_000


def test_same_seed_is_deterministic():
    a = simulate(trials=200, days=5, seed=7)
    b = simulate(trials=200, days=5, seed=7)
    assert a == b


def test_different_seed_differs():
    a = simulate(trials=200, days=5, seed=1)
    b = simulate(trials=200, days=5, seed=2)
    assert a["mean_final"] != b["mean_final"]


def test_breakeven_ladder_is_far_cheaper_than_target_ladder():
    """The whole reason for the break-even mode: under 'target', Groups 4-6
    cost more than the bankroll and so can never complete."""
    for g in GROUPS[3:]:                       # groups 4, 5, 6
        _, target_total = ladder_cost(g, RISE_FALL_PAYOUT, "target")
        _, breakeven_total = ladder_cost(g, RISE_FALL_PAYOUT, "breakeven")
        assert target_total > 10_000, f"group {g.number} target ladder unexpectedly fits"
        assert breakeven_total < 10_000, f"group {g.number} breakeven ladder does not fit"
        assert breakeven_total < target_total / 2


def test_reproduces_the_observed_group5_stake():
    """Account 2 really did reach a 2,907.26 stake at rung 8 of Group 5 from a
    -2,430.31 deficit, at the quoted 1.9240 payout. Anchor the sizing rule to
    that so a refactor cannot silently change it."""
    gl = GroupLadder(recovery_mode="target")
    gl.current_group_index = 4
    gl.states[5].cumulative_profit = -2430.31
    gl.states[5].trade_number = 8
    assert gl.next_stake(1.9240) == pytest.approx(2907.26, abs=0.02)


def test_breakeven_shrinks_that_same_stake():
    gl = GroupLadder(recovery_mode="breakeven")
    gl.current_group_index = 4
    gl.states[5].cumulative_profit = -2430.31
    gl.states[5].trade_number = 8
    assert gl.next_stake(1.9240) == pytest.approx(2630.21, abs=0.02)


def test_abandonment_keeps_trading_and_records_the_write_off():
    """on_exhaust='reset' must clear the group but never hide the loss."""
    one = (GroupSpec(1, (5.0, 10.0, 20.0, 40.0), 20.0),)
    gl = GroupLadder(groups=one, recovery_mode="breakeven", on_exhaust="reset")
    info = {}
    for _ in range(gl.max_trades_per_run):
        s = gl.next_stake(RISE_FALL_PAYOUT)
        info = gl.record_result(s, -s)

    assert gl.exhausted is False            # still alive
    assert info.get("abandoned") is True
    assert info["written_off"] > 0
    st = gl.states[1]
    assert st.trade_number == 1             # back to the first rung
    assert st.cumulative_profit == pytest.approx(0.0)
    assert st.abandoned_runs == 1
    assert st.abandoned_losses == pytest.approx(info["written_off"])


def test_stop_mode_still_halts():
    one = (GroupSpec(1, (5.0, 10.0, 20.0, 40.0), 20.0),)
    gl = GroupLadder(groups=one, recovery_mode="breakeven", on_exhaust="stop")
    for _ in range(gl.max_trades_per_run):
        s = gl.next_stake(RISE_FALL_PAYOUT)
        gl.record_result(s, -s)
    assert gl.exhausted is True


def test_abandonment_survives_longer_than_stopping():
    common = dict(recovery_mode="breakeven", trials=400, days=60,
                  trades_per_day=40, seed=3)
    stopped = simulate(on_exhaust="stop", **common)
    reset = simulate(on_exhaust="reset", **common)
    assert reset["median_days_survived"] > stopped["median_days_survived"]


def test_rejects_unknown_modes():
    with pytest.raises(ValueError):
        GroupLadder(recovery_mode="nonsense")
    with pytest.raises(ValueError):
        GroupLadder(on_exhaust="nonsense")


def test_config_is_not_restored_from_state():
    """A design change must take effect on restart rather than being pinned by
    an old state file - config comes from the constructor, not the JSON."""
    saved = GroupLadder(recovery_mode="target").to_dict()
    restored = GroupLadder.from_dict(saved, recovery_mode="breakeven")
    assert restored.recovery_mode == "breakeven"
