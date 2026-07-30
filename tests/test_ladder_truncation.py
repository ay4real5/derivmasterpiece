"""A progressive stake that does not fit must be refused, never truncated.

Observed live on 2026-07-30. The ladder ran 14.10 -> 29.36 -> 61.13, paying
104.59 to get there, then wanted 127.29 with 14.45 of daily budget left - and
staked 14.45. The platform showed 14.10, 29.36, 61.13, 14.45, which is not a
ladder. Even a WIN on that rung returns ~13.34 against 104.59 already lost.
"""
import pytest

from deriv_bot.staking import (
    DoublingMartingale,
    FlatStake,
    RecoveryLadder,
    fit_or_refuse,
)

SEQ = [3, 3.25, 6.77, 14.10, 29.36, 61.13, 127.29, 265.05]


def ladder(losses=0):
    s = RecoveryLadder(assumed_net_multiplier=0.9231, reset_after_losses=8,
                       sequence=list(SEQ))
    for _ in range(losses):
        s.record(-1.0)
    return s


# --- the exact live failure ------------------------------------------------

def test_the_observed_case_refuses_instead_of_staking_14_45():
    """Six losses in, the ladder wants 127.29. Budget left is 14.45."""
    s = ladder(losses=6)
    assert s.stake_for(3.0, 0.9231, budget_left=1000.0) == pytest.approx(127.29)
    assert s.stake_for(3.0, 0.9231, budget_left=14.45) == 0.0


def test_a_rung_that_exactly_fits_is_still_taken():
    """Refusal must trigger on 'does not fit', not on 'is close'."""
    s = ladder(losses=6)
    assert s.stake_for(3.0, 0.9231, budget_left=127.29) == pytest.approx(127.29)


def test_one_cent_short_refuses():
    s = ladder(losses=6)
    assert s.stake_for(3.0, 0.9231, budget_left=127.28) == 0.0


def test_every_rung_is_all_or_nothing():
    for i, rung in enumerate(SEQ):
        s = ladder(losses=i)
        assert s.stake_for(3.0, 0.9231, budget_left=rung) == pytest.approx(rung)
        assert s.stake_for(3.0, 0.9231, budget_left=rung - 0.01) == 0.0


def test_no_returned_stake_is_ever_a_non_rung_amount():
    """The bug's signature: a stake that appears nowhere in the sequence."""
    for i in range(len(SEQ)):
        for budget in (0.0, 5.0, 14.45, 53.18, 100.0, 500.0, 10_000.0):
            got = ladder(losses=i).stake_for(3.0, 0.9231, budget)
            assert got == 0.0 or any(abs(got - r) < 0.005 for r in SEQ), (
                f"rung {i} with budget {budget} produced {got}, not a ladder rung")


# --- the other progressive stakers share the premise -----------------------

def test_doubling_martingale_also_refuses_a_truncated_step():
    s = DoublingMartingale(multiplier=2.0)
    for _ in range(3):
        s.record(-1.0)
    assert s.stake_for(3.0, 0.9231, budget_left=1000.0) == pytest.approx(24.0)
    assert s.stake_for(3.0, 0.9231, budget_left=10.0) == 0.0


# --- flat staking is deliberately NOT changed ------------------------------

def test_flat_staking_still_truncates_because_it_has_no_recovery_premise():
    """A smaller flat bet is still a valid flat bet. Refusing here would stop
    the bot for the day over a budget that can still fund a real trade."""
    assert FlatStake().stake_for(3.0, 0.9231, budget_left=1.5) == pytest.approx(1.5)


# --- the helper itself -----------------------------------------------------

def test_fit_or_refuse_refuses_only_when_progressive():
    assert fit_or_refuse(100.0, 50.0, progressive=True) == 0.0
    assert fit_or_refuse(100.0, 50.0, progressive=False) == pytest.approx(50.0)


def test_fit_or_refuse_passes_a_fitting_stake_through():
    assert fit_or_refuse(40.0, 50.0, progressive=True) == pytest.approx(40.0)


def test_fit_or_refuse_never_returns_negative():
    assert fit_or_refuse(-5.0, 50.0, progressive=True) == 0.0
    assert fit_or_refuse(10.0, -3.0, progressive=True) == 0.0


def test_a_refused_stake_stops_the_session():
    """The caller treats a sub-minimum stake as 'stop for the day', so 0 has
    to be below MIN_STAKE or the refusal would silently become a tiny bet."""
    from main import MIN_STAKE
    assert 0.0 < MIN_STAKE


# --- the nine-rung ladder with a repeated top -------------------------------

SEQ9 = [3, 3.25, 6.77, 14.10, 29.36, 61.13, 127.29, 265.05, 265.05]


def ladder9(losses=0):
    s = RecoveryLadder(assumed_net_multiplier=0.9233, reset_after_losses=9,
                       sequence=list(SEQ9))
    for _ in range(losses):
        s.record(-1.0)
    return s


def test_the_ninth_rung_repeats_the_eighth_rather_than_exceeding_it():
    """A true ninth would be 509.95 / 0.9233 = 552.31. The cap on stake size
    is the whole point of repeating instead."""
    assert ladder9(losses=7).stake_for(3.0, 0.9233, 10_000.0) == pytest.approx(265.05)
    assert ladder9(losses=8).stake_for(3.0, 0.9233, 10_000.0) == pytest.approx(265.05)


def test_no_rung_ever_exceeds_the_capped_maximum():
    for i in range(len(SEQ9) + 3):
        got = ladder9(losses=i).stake_for(3.0, 0.9233, 10_000.0)
        assert got <= 265.05 + 0.005, f"rung {i} staked {got}"


def test_it_wraps_to_the_base_after_nine_losses():
    assert ladder9(losses=9).stake_for(3.0, 0.9233, 10_000.0) == pytest.approx(3.0)


def test_a_win_at_any_rung_resets_to_base():
    s = ladder9(losses=5)
    s.record(+10.0)
    assert s.stake_for(3.0, 0.9233, 10_000.0) == pytest.approx(3.0)


def test_the_full_cycle_fits_the_configured_daily_cap():
    """A ladder that cannot complete inside the cap takes every loss climbing
    and then has its recovery rung refused - hit twice in this project."""
    import yaml
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    seq = cfg["staking"]["sequence"]
    cap = cfg["risk"]["max_daily_loss"]
    assert sum(seq) < cap, f"ladder {sum(seq)} does not fit cap {cap}"


def test_the_shipped_ladder_matches_the_nine_rung_shape():
    import yaml
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    seq = cfg["staking"]["sequence"]
    assert len(seq) == 9
    assert cfg["staking"]["reset_after_losses"] == len(seq)
    assert seq[-1] == seq[-2], "the ninth rung must repeat the eighth"


def test_rungs_two_to_eight_still_recover_their_run():
    """The repeat breaks the recovery property at rung 9 ONLY - every earlier
    rung must still win back everything staked before it.

    Checked to 1%, not to the cent. The sequence is an explicit rounded ladder
    (each rung is a real stake, quoted to 2dp) originally derived at
    net=0.9231, so recomputing it from 0.9233 drifts slightly and the drift
    compounds - the config says as much. Asserting to the cent would be
    testing the arithmetic of the derivation rather than the property that
    matters, which is that each rung really does cover the run before it.
    """
    net = 0.9233
    for i in range(1, 8):
        want = sum(SEQ9[:i]) / net
        assert SEQ9[i] == pytest.approx(want, rel=0.01), (
            f"rung {i + 1} is {SEQ9[i]}, needs about {want:.2f} to recover "
            f"the {sum(SEQ9[:i]):.2f} staked before it")


def test_the_ninth_rung_is_knowingly_a_partial_recovery():
    """Documents the accepted trade-off rather than pretending it recovers."""
    net = 0.9233
    down_after_8 = sum(SEQ9[:8])
    recovered = SEQ9[8] * net
    assert recovered < down_after_8
    assert down_after_8 - recovered == pytest.approx(265.23, abs=0.5)
