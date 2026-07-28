import random

import pytest

from deriv_bot.study import (
    choose,
    digits_from_ticks,
    observed_ev,
    score_leg,
    score_legs,
    summarise,
)


def _quote(kind, barrier, payout=3.91, ask=2.0):
    return {"contract_type": kind, "barrier": barrier, "payout": payout, "ask_price": ask}


def test_digits_from_ticks_takes_the_last_digit_at_pip_precision():
    assert digits_from_ticks([761.03, 761.28, 760.94], pip_size=2) == [3, 8, 4]


def test_digits_from_ticks_recovers_a_trailing_zero():
    # 531.70 arrives from JSON as 531.7; str()[-1] would read 7 and the
    # digit 0 would never appear, biasing every Over/Under score
    assert digits_from_ticks([531.7, 531.70, 100.0], pip_size=2) == [0, 0, 0]


def test_digits_from_ticks_respects_a_different_pip_size():
    assert digits_from_ticks([1.23456, 1.20000], pip_size=5) == [6, 0]


def test_digits_from_ticks_skips_unusable_entries():
    assert digits_from_ticks([761.03, None, "abc"], pip_size=2) == [3]


def test_score_leg_measures_observed_against_theoretical():
    digits = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]  # exactly uniform
    row = score_leg(digits, "DIGITEVEN", None)
    assert row["n"] == 10
    assert row["hits"] == 5
    assert row["observed"] == 0.5
    assert row["expected"] == 0.5
    assert row["z"] == pytest.approx(0.0)


def test_score_leg_detects_a_deliberately_biased_stream():
    # every digit even: DIGITEVEN wins 100% against an expected 50%
    digits = [0, 2, 4, 6, 8] * 40  # n=200
    row = score_leg(digits, "DIGITEVEN", None)
    assert row["observed"] == 1.0
    assert row["z"] > 10  # unmistakable


def test_score_leg_returns_none_for_price_resolved_legs():
    # CALL/PUT resolve on price, so digit history says nothing about them
    assert score_leg([1, 2, 3], "CALL", None) is None
    assert score_leg([1, 2, 3], "PUT", None) is None


def test_score_leg_returns_none_without_digits():
    assert score_leg([], "DIGITEVEN", None) is None


def test_score_legs_skips_unscoreable_and_keeps_the_rest():
    scored = score_legs([0, 2, 4, 6, 8] * 10,
                        [("DIGITEVEN", None), ("CALL", None), ("DIGITOVER", "4")])
    kinds = {r["contract_type"] for r in scored}
    assert kinds == {"DIGITEVEN", "DIGITOVER"}


def test_choose_abstains_on_a_fair_random_stream():
    # THE test that matters: uniform noise must not produce a confident pick
    rng = random.Random(12345)
    digits = [rng.randrange(10) for _ in range(200)]
    legs = [("DIGITEVEN", None), ("DIGITODD", None), ("DIGITOVER", "4"), ("DIGITUNDER", "4")]
    scored = score_legs(digits, legs)
    quotes = {(k, b): _quote(k, b) for k, b in legs}
    winner, why = choose(scored, quotes, min_z=2.0)
    assert winner is None
    assert "ABSTAINED" in why


def test_choose_picks_a_genuinely_biased_leg():
    digits = [0, 2, 4, 6, 8] * 40
    legs = [("DIGITEVEN", None), ("DIGITODD", None)]
    scored = score_legs(digits, legs)
    quotes = {(k, b): _quote(k, b) for k, b in legs}
    winner, why = choose(scored, quotes, min_z=2.0)
    assert winner is not None
    assert winner["contract_type"] == "DIGITEVEN"
    assert "picked" in why


def test_choose_reversion_prefers_the_underperforming_leg():
    digits = [0, 2, 4, 6, 8] * 40  # DIGITODD never won
    legs = [("DIGITEVEN", None), ("DIGITODD", None)]
    scored = score_legs(digits, legs)
    quotes = {(k, b): _quote(k, b) for k, b in legs}
    winner, _ = choose(scored, quotes, min_z=2.0, mode="reversion")
    assert winner is not None
    assert winner["contract_type"] == "DIGITODD"


def test_choose_abstains_when_no_scored_leg_has_a_quote():
    scored = score_legs([0, 2, 4, 6, 8] * 40, [("DIGITEVEN", None)])
    winner, why = choose(scored, {}, min_z=2.0)
    assert winner is None
    assert "no scored leg had a live quote" in why


def test_choose_abstains_with_nothing_scoreable():
    winner, why = choose([], {}, min_z=2.0)
    assert winner is None
    assert "nothing scoreable" in why


def test_observed_ev_uses_the_real_quoted_payout():
    row = score_leg([0, 2, 4, 6, 8] * 40, "DIGITEVEN", None)  # observed 100%
    # pays 3.91 on a 2.00 stake -> +95.5% per $1 if it really won every time
    assert observed_ev(row, _quote("DIGITEVEN", None)) == pytest.approx(0.955, abs=1e-3)


def test_observed_ev_handles_a_zero_ask():
    row = score_leg([0, 2, 4], "DIGITEVEN", None)
    assert observed_ev(row, _quote("DIGITEVEN", None, ask=0.0)) == 0.0


def test_summarise_lists_every_scored_leg():
    scored = score_legs([0, 2, 4, 6, 8] * 10, [("DIGITEVEN", None), ("DIGITODD", None)])
    text = summarise(scored)
    assert "DIGITEVEN" in text and "DIGITODD" in text


def test_summarise_with_nothing_scored():
    assert summarise([]) == "study: no scoreable legs"
