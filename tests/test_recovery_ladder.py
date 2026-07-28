import pytest

from deriv_bot.staking import RecoveryLadder, build_staker

SEQ = [3, 3.25, 6.77, 14.10, 29.36, 61.13, 127.29, 265.05]


def _walk(s, n, base=3.0, net=0.9231):
    out = []
    for _ in range(n):
        stake = s.stake_for(base, net, 1e9)
        out.append(stake)
        s.record(-stake)
    return out


def test_explicit_sequence_is_reproduced_exactly():
    # explicit, because recomputing compounds rounding and drifts ~0.2% by
    # the 8th rung - and this is the number the worst case is built on
    s = RecoveryLadder(sequence=SEQ, reset_after_losses=8)
    assert _walk(s, 8) == SEQ


def test_full_ladder_cost():
    assert sum(SEQ) == pytest.approx(509.95)


def test_it_wraps_back_to_base_after_the_last_rung():
    s = RecoveryLadder(sequence=SEQ, reset_after_losses=8)
    got = _walk(s, 11)
    assert got[8:] == [3, 3.25, 6.77]


def test_a_win_resets_to_base():
    s = RecoveryLadder(sequence=SEQ, reset_after_losses=8)
    s.record(-3.0)
    s.record(-3.25)
    assert s.stake_for(3.0, 0.9231, 1e9) == 6.77
    s.record(+6.0)
    assert s.stake_for(3.0, 0.9231, 1e9) == 3


def test_computed_sequence_approximates_the_explicit_one():
    # without `sequence`, stakes are derived from cycle_loss / assumed net
    s = RecoveryLadder(assumed_net_multiplier=0.9231, reset_after_losses=8)
    got = _walk(s, 8)
    for a, b in zip(got, SEQ):
        assert a == pytest.approx(b, rel=0.005)   # within 0.5%


def test_recovery_returns_zero_at_the_assumed_payout():
    """The property that matters: sized for 0.9231, a win on rungs 2+ returns
    exactly nothing. Only a first-bet win profits."""
    cum = 0.0
    for i, stake in enumerate(SEQ):
        result = stake * 0.9231 - cum
        if i == 0:
            assert result == pytest.approx(2.77, abs=0.01)
        else:
            assert result == pytest.approx(0.0, abs=0.25)
        cum += stake


def test_recovery_overshoots_into_profit_on_cheaper_contracts():
    """And why it pairs with global_best selection: on a 2.25% contract
    (net 0.954) every recovery rung over-recovers."""
    cum = 0.0
    for i, stake in enumerate(SEQ):
        result = stake * 0.954 - cum
        assert result > 0
        cum += stake


def test_budget_left_caps_the_stake():
    s = RecoveryLadder(sequence=SEQ, reset_after_losses=8)
    for _ in range(7):
        s.record(-1.0)
    assert s.stake_for(3.0, 0.9231, budget_left=50.0) == 50.0


def test_rejects_bad_parameters():
    with pytest.raises(ValueError):
        RecoveryLadder(assumed_net_multiplier=0)
    with pytest.raises(ValueError):
        RecoveryLadder(assumed_net_multiplier=1.5)
    with pytest.raises(ValueError):
        RecoveryLadder(reset_after_losses=0)
    with pytest.raises(ValueError):
        RecoveryLadder(sequence=[])
    with pytest.raises(ValueError):
        RecoveryLadder(sequence=[3, -1])
    with pytest.raises(ValueError):
        RecoveryLadder(cycle_profit=-1)


def test_build_staker_knows_the_name():
    s = build_staker("recovery_ladder", sequence=SEQ, reset_after_losses=8)
    assert isinstance(s, RecoveryLadder)


def test_it_never_overrides_the_contract_choice():
    from deriv_bot.strategy import Signal
    s = RecoveryLadder(sequence=SEQ)
    s.record(-3.0)
    assert s.override_signal(Signal("DIGITOVER", "4", "x")) is None
