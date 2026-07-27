import pytest

from deriv_bot.staking import (
    DoublingMartingale,
    FlatStake,
    RecoveryMartingale,
    SmartRecoveryMartingale,
    build_staker,
)
from deriv_bot.strategy import Signal


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


def test_base_staker_never_overrides():
    assert RecoveryMartingale().override_signal(Signal("DIGITOVER", "0", "x")) is None
    assert FlatStake().override_signal(Signal("DIGITOVER", "0", "x")) is None


def test_smart_recovery_leaves_fresh_bets_alone():
    s = SmartRecoveryMartingale()
    signal = Signal("DIGITOVER", "0", "quota pick")
    assert s.override_signal(signal) is None  # cycle_loss is 0 -> no override


def test_smart_recovery_switches_contract_during_a_losing_cycle():
    s = SmartRecoveryMartingale(recovery_contracts=["DIGITEVEN", "DIGITODD"])
    s.record(-35.0)
    override = s.override_signal(Signal("DIGITOVER", "0", "quota pick"))
    assert override is not None
    assert override.contract_type in ("DIGITEVEN", "DIGITODD")
    assert override.barrier is None


def test_smart_recovery_cycles_through_recovery_legs():
    s = SmartRecoveryMartingale(recovery_contracts=["DIGITEVEN", "DIGITODD"])
    s.record(-35.0)
    sig = Signal("DIGITOVER", "0", "quota pick")
    first = s.override_signal(sig).contract_type
    second = s.override_signal(sig).contract_type
    third = s.override_signal(sig).contract_type
    assert [first, second, third] == ["DIGITEVEN", "DIGITODD", "DIGITEVEN"]


def test_smart_recovery_still_sizes_by_the_overridden_contracts_net():
    # sanity check on the real motivating scenario: recovering a $35 loss
    # on a 50%-tier contract (net 0.923) is far cheaper than on a 90%-tier
    # one (net 0.087)
    s = SmartRecoveryMartingale()
    s.record(-35.0)
    cheap_stake = s.stake_for(35.0, 0.923, budget_left=10_000)
    expensive_stake = s.stake_for(35.0, 0.087, budget_left=10_000)
    assert cheap_stake < expensive_stake / 5


def test_smart_recovery_rejects_bad_contract_spec():
    with pytest.raises(ValueError):
        SmartRecoveryMartingale(recovery_contracts=["NOTREAL"])
    with pytest.raises(ValueError):
        SmartRecoveryMartingale(recovery_contracts=["DIGITOVER"])  # needs a barrier
    with pytest.raises(ValueError):
        SmartRecoveryMartingale(recovery_contracts=["DIGITOVER:99"])


def test_doubling_produces_the_classic_sequence():
    s = DoublingMartingale()
    base = 10.0
    stakes = []
    for _ in range(6):
        stakes.append(s.stake_for(base, 0.923, budget_left=10_000))
        s.record(-1.0)  # any loss
    assert stakes == [10.0, 20.0, 40.0, 80.0, 160.0, 320.0]


def test_doubling_resets_on_win():
    s = DoublingMartingale()
    s.record(-1.0)
    s.record(-1.0)
    assert s.stake_for(10.0, 0.923, 10_000) == 40.0
    s.record(1.0)
    assert s.stake_for(10.0, 0.923, 10_000) == 10.0


def test_doubling_capped_by_max_stake_multiple():
    s = DoublingMartingale(max_stake_multiple=32)
    for _ in range(10):
        s.record(-1.0)
    assert s.stake_for(10.0, 0.923, budget_left=100_000) == 320.0  # 32 x 10


def test_doubling_capped_by_budget():
    s = DoublingMartingale()
    for _ in range(3):
        s.record(-1.0)
    assert s.stake_for(10.0, 0.923, budget_left=50.0) == 50.0


def test_doubling_never_overrides_contract():
    s = DoublingMartingale()
    s.record(-1.0)
    assert s.override_signal(Signal("DIGITOVER", "0", "quota pick")) is None


def test_doubling_start_streak_resumes_a_previous_ladder():
    # base 2 with six losses already booked opens at 2 * 2**6 = 128,
    # then climbs 256, 512 like any other continuation of the sequence.
    s = DoublingMartingale(max_stake_multiple=256, start_streak=6)
    stakes = []
    for _ in range(3):
        stakes.append(s.stake_for(2.0, 0.923, budget_left=10_000))
        s.record(-1.0)
    assert stakes == [128.0, 256.0, 512.0]


def test_doubling_start_streak_still_resets_to_base_on_a_win():
    s = DoublingMartingale(start_streak=6)
    assert s.stake_for(2.0, 0.923, 10_000) == 128.0
    s.record(1.0)
    assert s.stake_for(2.0, 0.923, 10_000) == 2.0


def test_doubling_start_streak_defaults_to_a_fresh_ladder():
    assert DoublingMartingale().stake_for(2.0, 0.923, 10_000) == 2.0


def test_doubling_reset_after_losses_wraps_the_ladder_back_to_base():
    # 5,10,20,40,80,160,320 then start over at 5 rather than pinning at 320
    s = DoublingMartingale(reset_after_losses=7)
    stakes = []
    for _ in range(10):
        stakes.append(s.stake_for(5.0, 0.923, budget_left=100_000))
        s.record(-1.0)
    assert stakes[:7] == [5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 320.0]
    assert stakes[7:] == [5.0, 10.0, 20.0]  # wrapped, not stuck at the ceiling


def test_doubling_reset_after_losses_costs_the_full_ladder():
    # one completed ladder books 635 as a realised loss; the next $5 bet is
    # not trying to win it back
    assert sum([5, 10, 20, 40, 80, 160, 320]) == 635


def test_doubling_reset_after_losses_still_resets_on_a_win():
    s = DoublingMartingale(reset_after_losses=7)
    for _ in range(3):
        s.record(-1.0)
    assert s.stake_for(5.0, 0.923, 100_000) == 40.0
    s.record(1.0)
    assert s.stake_for(5.0, 0.923, 100_000) == 5.0


def test_doubling_without_reset_after_losses_is_unchanged():
    s = DoublingMartingale(max_stake_multiple=64)
    for _ in range(9):
        s.record(-1.0)
    assert s.stake_for(5.0, 0.923, 100_000) == 320.0  # pinned at the cap, not wrapped


def test_doubling_rejects_bad_parameters():
    with pytest.raises(ValueError):
        DoublingMartingale(multiplier=1.0)
    with pytest.raises(ValueError):
        DoublingMartingale(max_stake_multiple=0.5)
    with pytest.raises(ValueError):
        DoublingMartingale(start_streak=-1)
    with pytest.raises(ValueError):
        DoublingMartingale(reset_after_losses=0)


def test_build_staker_doubling():
    s = build_staker("doubling", multiplier=2.0)
    assert isinstance(s, DoublingMartingale)
