import pytest

from deriv_bot.staking import FlatStake, RecoveryMartingale, build_staker


def test_flat_always_base_stake():
    s = FlatStake()
    assert s.stake_for(35.0, 0.923, budget_left=800) == 35.0
    s.record(-35.0)
    assert s.stake_for(35.0, 0.923, budget_left=800) == 35.0


def test_flat_capped_by_budget():
    assert FlatStake().stake_for(35.0, 0.923, budget_left=10.0) == 10.0


def test_martingale_recovers_losing_run():
    s = RecoveryMartingale()
    assert s.stake_for(35.0, 0.923, 800) == pytest.approx(35.0)
    s.record(-35.0)
    # a win must recover the 35 lost plus one base profit
    stake = s.stake_for(35.0, 0.923, 800)
    assert stake * 0.923 == pytest.approx(35.0 + 35.0 * 0.923)


def test_partial_recovery_stakes_less_than_full():
    full, half = RecoveryMartingale(), RecoveryMartingale(recovery_fraction=0.5)
    for s in (full, half):
        s.record(-100.0)
    assert half.stake_for(35.0, 0.923, 800) < full.stake_for(35.0, 0.923, 800)
    # half recovers exactly half the run
    assert half.stake_for(35.0, 0.923, 800) == pytest.approx(35.0 + 0.5 * 100 / 0.923)


def test_recovery_fraction_zero_is_flat():
    s = RecoveryMartingale(recovery_fraction=0.0)
    s.record(-500.0)
    assert s.stake_for(35.0, 0.923, 800) == pytest.approx(35.0)


def test_max_stake_multiple_caps_the_blowup():
    s = RecoveryMartingale(max_stake_multiple=20)
    for _ in range(5):
        s.record(-200.0)
    assert s.stake_for(35.0, 0.087, budget_left=10_000) == pytest.approx(700.0)


def test_rejects_bad_parameters():
    with pytest.raises(ValueError):
        RecoveryMartingale(recovery_fraction=1.5)
    with pytest.raises(ValueError):
        RecoveryMartingale(max_stake_multiple=0.5)


def test_martingale_resets_after_win():
    s = RecoveryMartingale()
    s.record(-35.0)
    s.record(-70.0)
    assert s.stake_for(35.0, 0.923, 800) > 100
    s.record(50.0)
    assert s.stake_for(35.0, 0.923, 800) == pytest.approx(35.0)


def test_martingale_never_exceeds_budget():
    s = RecoveryMartingale()
    for _ in range(6):
        s.record(-100.0)
    assert s.stake_for(35.0, 0.923, budget_left=120.0) == 120.0


def test_martingale_explodes_on_high_prob_contracts():
    # 90% contract pays only 8.7% — recovering one $35 loss needs ~$402
    s = RecoveryMartingale()
    s.record(-35.0)
    assert s.stake_for(35.0, 0.087, budget_left=10_000) == pytest.approx(437.3, abs=1.0)


def test_build_staker_rejects_unknown():
    with pytest.raises(ValueError):
        build_staker("kelly-on-vibes")
