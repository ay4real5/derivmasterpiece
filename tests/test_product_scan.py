import pytest

from deriv_bot.product_scan import (
    categories_available,
    digit_pair_barriers,
    pair_cost,
    pair_weights,
    pairs_to_price,
    payout_ratio,
    roundtrip_cost,
)


def q(payout, ask):
    return {"payout": payout, "ask_price": ask}


# --- the gate: reproduce edges we already know exactly ------------------
# If the model-free method cannot recover numbers derived from exact digit
# arithmetic, it cannot be believed on products where exact maths is
# unavailable.

def test_reproduces_the_known_50_percent_digit_edge():
    # R_10: DIGITOVER:4 and DIGITUNDER:5 each pay 5.86 on 3.00, 2.33% edge
    r = pair_cost(q(5.86, 3.00), q(5.86, 3.00))
    assert r["cost_pct"] == pytest.approx(2.33, abs=0.02)


def test_reproduces_the_known_over0_match0_hedge():
    # R_10: DIGITOVER:0 pays 3.29, DIGITMATCH:0 pays 25.00, both on 3.00
    r = pair_cost(q(3.29, 3.00), q(25.00, 3.00))
    assert r["cost_pct"] == pytest.approx(3.09, abs=0.02)


def test_pair_cost_is_not_the_same_as_single_leg_edge():
    """A distinction worth a test of its own, because conflating them is an
    easy and expensive mistake. On R_10 DIGITOVER:0 alone charges 1.30% and
    DIGITMATCH:0 alone charges 16.67%, yet covering both costs 3.09% - a
    stake-weighted blend, since most of the money goes on the cheap leg."""
    r = pair_cost(q(3.29, 3.00), q(25.00, 3.00))
    assert 1.30 < r["cost_pct"] < 16.67
    assert r["cost_pct"] == pytest.approx(3.09, abs=0.02)


def test_two_same_side_contracts_are_not_a_valid_pair():
    # DIGITOVER:0 and DIGITUNDER:9 both win 90% of the time and overlap
    # heavily - pairing them is meaningless, and the maths says so loudly
    r = pair_cost(q(3.29, 3.00), q(3.29, 3.00))
    assert r["cost_pct"] > 40


# --- the weighting ------------------------------------------------------

def test_weights_equalise_both_outcomes():
    a, b = pair_weights(q(3.29, 3.00), q(25.00, 3.00))
    payout_if_a_wins = a * payout_ratio(q(3.29, 3.00))
    payout_if_b_wins = b * payout_ratio(q(25.00, 3.00))
    assert payout_if_a_wins == pytest.approx(payout_if_b_wins, abs=0.01)


def test_equal_quotes_get_equal_stakes():
    a, b = pair_weights(q(5.86, 3.00), q(5.86, 3.00))
    assert a == pytest.approx(b)


def test_payout_ratio_rejects_a_zero_ask():
    with pytest.raises(ValueError):
        payout_ratio(q(5.0, 0.0))


# --- what a mispricing would look like ---------------------------------

def test_a_fair_pair_costs_nothing():
    # two 50% contracts paying exactly 2x would be a zero-margin venue
    r = pair_cost(q(6.00, 3.00), q(6.00, 3.00))
    assert r["cost_pct"] == pytest.approx(0.0, abs=1e-9)


def test_a_mispriced_pair_shows_negative_cost():
    # the one result worth acting on - and worth distrusting until repeated
    r = pair_cost(q(6.20, 3.00), q(6.20, 3.00))
    assert r["cost_pct"] < 0
    assert r["profit"] > 0


# --- digit barrier pairing ---------------------------------------------

def test_over_under_complement_is_offset_by_one():
    # DIGITOVER:4 wins on 5-9, so its complement is DIGITUNDER:5, not :4
    assert digit_pair_barriers("DIGITOVER", "DIGITUNDER", 4) == ("4", "5")


def test_match_diff_share_a_barrier():
    assert digit_pair_barriers("DIGITMATCH", "DIGITDIFF", 7) == ("7", "7")


# --- discovery ----------------------------------------------------------

def test_categories_available_parses_the_response():
    resp = {"contracts_for": {"available": [
        {"contract_category": "digits", "contract_type": "DIGITEVEN"},
        {"contract_category": "digits", "contract_type": "DIGITODD"},
        {"contract_category": "turbos", "contract_type": "TURBOSLONG"},
    ]}}
    cats = categories_available(resp)
    assert cats["digits"] == {"DIGITEVEN", "DIGITODD"}
    assert cats["turbos"] == {"TURBOSLONG"}


def test_only_pairs_the_venue_offers_are_priced():
    available = {"digits": {"DIGITEVEN", "DIGITODD"},
                 "touchnotouch": {"ONETOUCH"}}       # NOTOUCH missing
    pairs = pairs_to_price(available)
    assert ("digits", "DIGITEVEN", "DIGITODD") in pairs
    assert not any(c == "touchnotouch" for c, _, _ in pairs)


def test_roundtrip_reports_its_basis():
    r = roundtrip_cost({"ask_price": 10.0, "commission": 0.05},
                       {"ask_price": 10.0, "commission": 0.05})
    assert r["cost_pct"] == pytest.approx(0.5)
    assert "explicit commission" in r["basis"]


def test_roundtrip_says_when_no_commission_is_reported():
    r = roundtrip_cost({"ask_price": 10.0}, {"ask_price": 10.0})
    assert r["commission"] == 0
    assert "no commission" in r["basis"]
