"""The two known-answer checks that caught the Jensen bug, plus the rest.

The simulation manufactured a 13% edge from a zero-margin zero-skill setup
before these existed. Any number it produces is only quotable because these
pass.
"""
import math

import pytest

from pricebot.vol_trade import (
    expected_return_per_trade,
    expected_touch_probability,
    house_margin,
    max_supportable_margin,
    norm_cdf,
    touch_probability,
)

PHI, SD, MU = 0.607, 0.600, math.log(0.02535)   # measured silver parameters


# --- the checks that matter ------------------------------------------------

def test_no_margin_no_skill_returns_exactly_zero():
    """A fair bet with no information must return 0. This is the check that
    exposed pricing the quote at the median volatility instead of the mean."""
    r = expected_return_per_trade(0.0, PHI, SD, MU, skill=0.0, trials=200000)
    assert abs(r) < 0.005


def test_margin_with_no_skill_returns_exactly_minus_margin_over_one_plus_margin():
    for m in (0.05, 0.1640, 0.30):
        r = expected_return_per_trade(m, PHI, SD, MU, skill=0.0, trials=150000)
        assert r == pytest.approx(-m / (1 + m), abs=0.006)


def test_zero_persistence_is_worthless_even_with_full_skill():
    """No clustering means nothing to forecast, so skill must not help."""
    r = expected_return_per_trade(0.0, 0.0, SD, MU, skill=1.0, trials=150000)
    assert abs(r) < 0.006


def test_skill_helps_monotonically():
    rs = [expected_return_per_trade(0.0, PHI, SD, MU, skill=s, trials=80000)
          for s in (0.0, 0.5, 1.0)]
    assert rs[0] < rs[1] < rs[2]


def test_more_persistence_supports_more_margin():
    a = max_supportable_margin(0.3, SD, MU, trials=40000)
    b = max_supportable_margin(0.6, SD, MU, trials=40000)
    assert b > a > 0


# --- the pieces ------------------------------------------------------------

def test_expected_touch_differs_from_touch_at_the_mean():
    """The whole bug in one assertion: E[f(X)] != f(E[X]) for this f."""
    d = 0.15 * 0.02535
    at_mean = touch_probability(d, math.exp(MU))
    expected = expected_touch_probability(d, MU, SD)
    assert abs(expected - at_mean) > 0.01


def test_expected_touch_collapses_to_point_value_with_no_dispersion():
    d = 0.5 * 0.02
    assert (expected_touch_probability(d, math.log(0.02), 0.0)
            == pytest.approx(touch_probability(d, 0.02)))


def test_grid_integrates_a_known_quantity():
    """E[X^2] for a standard normal is 1 - checks the quadrature weights."""
    from pricebot.vol_trade import _W, _X
    assert sum(w * x * x for x, w in zip(_X, _W)) == pytest.approx(1.0, abs=1e-6)
    assert sum(_W) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("d,sigma,expected", [
    (0.0, 0.01, 1.0),        # barrier at spot is touched immediately
    (10.0, 0.01, 0.0),       # unreachable
])
def test_touch_probability_limits(d, sigma, expected):
    assert touch_probability(d, sigma) == pytest.approx(expected, abs=1e-9)


def test_touch_probability_is_one_half_at_the_median_of_the_max():
    """P(touch) = 2*Phi(-d/sigma) = 0.5 exactly when d/sigma = -Phi^-1(0.25)."""
    z = 0.6744897501960817          # Phi^-1(0.75)
    assert touch_probability(z, 1.0) == pytest.approx(0.5, abs=1e-6)


def test_touch_probability_rises_as_the_barrier_narrows():
    assert (touch_probability(0.001, 0.01) > touch_probability(0.005, 0.01)
            > touch_probability(0.02, 0.01))


def test_touch_probability_zero_volatility_never_touches():
    assert touch_probability(0.01, 0.0) == 0.0


def test_norm_cdf_known_values():
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(1.96) == pytest.approx(0.975, abs=1e-4)
    assert norm_cdf(-1.96) == pytest.approx(0.025, abs=1e-4)


# --- the model-free margin -------------------------------------------------

def test_house_margin_is_zero_for_a_fair_pair():
    """Complementary contracts at fair odds: 1/p + 1/(1-p) payouts sum to 1."""
    p = 0.3
    assert house_margin(100 / p, 100 / (1 - p)) == pytest.approx(0.0, abs=1e-12)


def test_house_margin_matches_the_measured_gold_quote():
    """The real numbers: gold 1-day Touch/No Touch at a 0.227% barrier."""
    assert house_margin(102.71, 516.76) == pytest.approx(0.1671, abs=0.0002)


def test_house_margin_matches_the_measured_silver_quote():
    assert house_margin(101.23, 567.80) == pytest.approx(0.1640, abs=0.0002)


def test_house_margin_rejects_impossible_payouts():
    with pytest.raises(ValueError):
        house_margin(0.0, 100.0)
    with pytest.raises(ValueError):
        house_margin(100.0, -5.0)


def test_house_margin_scales_with_stake():
    assert (house_margin(2.0, 2.0, stake=1.0)
            == pytest.approx(house_margin(200.0, 200.0, stake=100.0)))


# --- the actual conclusion, locked in --------------------------------------

def test_silver_edge_does_not_cover_the_silver_fee():
    """The finding, as a regression test.

    Silver has the strongest volatility persistence of any symbol Deriv
    offers, measured at the tightest barrier it will quote, under assumptions
    chosen entirely in our favour. It still does not clear the fee.
    """
    supported = max_supportable_margin(PHI, SD, MU, barrier_over_vol=0.15,
                                       trials=80000)
    charged = 0.1640                      # measured, model-free
    assert supported < charged
    # ...but only just, which is the honest shape of the result
    assert supported > charged - 0.03
