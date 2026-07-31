"""Decide even-vs-rise each cycle from analysis, not from whose turn it is.

Before this, `categories: [even, rise]` alternated strictly: even, rise, even,
rise. The mix was decided by the rotation, not by anything measured - and it
could not have been otherwise, because `score_leg` returns None for CALL/PUT,
so the rise side of the book had NO analysis at all. Only the digit side was
ever scored.

Both contracts settle on the very next tick, so they are directly comparable:

    DIGITEVEN   is the next digit even?    expected 0.5
    CALL        is the next tick higher?   expected 0.5
"""
import math

import pytest

from deriv_bot.study import (
    choose, observed_ev, score_legs, score_price_leg, summarise,
)

EVEN = ("DIGITEVEN", None)
CALL = ("CALL", None)
PUT = ("PUT", None)


def rising(n=200):
    return [100.0 + i for i in range(n)]


def falling(n=200):
    return [100.0 - i for i in range(n)]


def alternating(n=200):
    out = [100.0]
    for i in range(n):
        out.append(out[-1] + (1 if i % 2 == 0 else -1))
    return out


# --- the rise side can now be scored at all --------------------------------

def test_call_is_scored_from_prices():
    row = score_price_leg(rising(), "CALL")
    assert row is not None
    assert row["contract_type"] == "CALL"
    assert row["observed"] == pytest.approx(1.0)
    assert row["expected"] == 0.5
    assert row["z"] > 5


def test_put_is_the_mirror_of_call():
    up = score_price_leg(rising(), "CALL")
    down = score_price_leg(rising(), "PUT")
    assert up["observed"] == pytest.approx(1.0)
    assert down["observed"] == pytest.approx(0.0)
    assert up["z"] == pytest.approx(-down["z"])


def test_a_digit_contract_is_rejected_by_the_price_scorer():
    assert score_price_leg(rising(), "DIGITEVEN") is None


def test_flat_ticks_are_excluded_not_counted_as_losses():
    """A tie loses BOTH CALL and PUT, so it belongs to neither hit rate.
    Counting it would drag both below 0.5 and invent a reversion signal."""
    prices = [100.0, 100.0, 101.0, 101.0, 102.0]     # 2 up, 2 flat
    row = score_price_leg(prices, "CALL")
    assert row["n"] == 2
    assert row["observed"] == pytest.approx(1.0)


def test_too_few_decisive_moves_returns_none_rather_than_a_wild_z():
    assert score_price_leg([100.0, 100.0, 100.0], "CALL") is None
    assert score_price_leg([100.0], "CALL") is None


def test_a_fair_walk_scores_near_fifty_percent():
    import random
    rng = random.Random(11)
    p = [100.0]
    for _ in range(4000):
        p.append(p[-1] + rng.choice([1, -1]))
    row = score_price_leg(p, "CALL")
    assert abs(row["observed"] - 0.5) < 0.03
    assert abs(row["z"]) < 3


# --- both sides on one table -----------------------------------------------

def test_score_legs_returns_both_sides_when_prices_are_supplied():
    digits = [0, 2, 4, 6, 8] * 40
    rows = score_legs(digits, [EVEN, CALL], prices=rising())
    kinds = {r["contract_type"] for r in rows}
    assert kinds == {"DIGITEVEN", "CALL"}


def test_without_prices_the_rise_side_is_skipped_exactly_as_before():
    """Every existing caller must keep its current behaviour."""
    digits = [0, 2, 4, 6, 8] * 40
    rows = score_legs(digits, [EVEN, CALL])
    assert [r["contract_type"] for r in rows] == ["DIGITEVEN"]


def test_the_study_table_shows_both_sides():
    digits = [0, 2, 4, 6, 8] * 40
    text = summarise(score_legs(digits, [EVEN, CALL], prices=rising()))
    assert "DIGITEVEN" in text and "CALL" in text


# --- the decision ----------------------------------------------------------

def quotes(payout=5.86, ask=3.0):
    q = {"payout": payout, "ask_price": ask}
    return {EVEN: q, CALL: {"payout": 5.77, "ask_price": ask}}


def test_the_stronger_side_wins_regardless_of_which_it_is():
    """The whole point: it can pick rise twice running, or even twice."""
    flat_digits = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9] * 20     # perfectly fair
    rows = score_legs(flat_digits, [EVEN, CALL], prices=rising())
    pick, why = choose(rows, quotes(), min_z=2.0)
    assert pick is not None and pick["contract_type"] == "CALL", why


def test_even_wins_when_the_digits_are_the_biased_side():
    even_digits = [0, 2, 4, 6, 8] * 40                    # 100% even
    rows = score_legs(even_digits, [EVEN, CALL], prices=alternating())
    pick, why = choose(rows, quotes(), min_z=2.0)
    assert pick is not None and pick["contract_type"] == "DIGITEVEN", why


def test_it_abstains_when_neither_side_clears_significance():
    """The expected outcome on real data, and NOT a failure - the caller then
    keeps its cheapest-margin fallback, which is the one measured edge."""
    import random
    rng = random.Random(5)
    digits = [rng.randrange(10) for _ in range(500)]
    p = [100.0]
    for _ in range(500):
        p.append(p[-1] + rng.choice([1, -1]))
    rows = score_legs(digits, [EVEN, CALL], prices=p)
    pick, why = choose(rows, quotes(), min_z=2.0)
    assert pick is None
    assert "ABSTAINED" in why and "cheapest quoted margin" in why


def test_the_two_sides_are_compared_on_the_same_scale():
    """Both are 1-tick binomials against 0.5, so a z from one is a z from the
    other. If the expected rates ever diverged, the comparison would be
    meaningless."""
    digits = [rng for rng in [0, 1] * 250]
    rows = score_legs(digits, [EVEN, CALL], prices=alternating())
    assert {r["expected"] for r in rows} == {0.5}


def test_ev_uses_the_real_quoted_payout_for_either_side():
    rows = score_legs([0, 2] * 100, [EVEN, CALL], prices=rising())
    for r in rows:
        q = quotes()[(r["contract_type"], r["barrier"])]
        ev = observed_ev(r, q)
        assert ev == pytest.approx(
            (r["observed"] * q["payout"] - q["ask_price"]) / q["ask_price"])


# --- the mode is wired -----------------------------------------------------

def test_signal_is_an_accepted_selection_mode():
    src = open("main.py", encoding="utf-8").read()
    assert '"global_best", "rotation", "signal"' in src
    assert 'selection_mode == "signal"' in src


def test_signal_mode_studies_every_configured_leg_not_just_one_categorys():
    """Under rotation the study only ever saw the leg whose turn it was, which
    is why the mix alternated no matter what the numbers said."""
    src = open("main.py", encoding="utf-8").read()
    assert 'study_legs = legs if selection_mode == "rotation" else list(candidates)' in src


def test_the_study_receives_prices_so_the_rise_side_is_scoreable():
    src = open("main.py", encoding="utf-8").read()
    assert "score_legs(digits, target_legs, prices=prices)" in src
