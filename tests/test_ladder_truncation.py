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
