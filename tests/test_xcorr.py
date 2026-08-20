"""The correlation test has to be trustworthy before any result from it is.

A test that reports an edge where none exists is worse than no test, because
this repo's whole method is to believe the measurement. So: known-answer cases
for the maths, and a planted-signal case proving it can actually SEE an edge.
"""
import math
import random

from tools.xcorr import (
    BREAK_EVEN, bonferroni_z, corr, profitable_r, returns,
)


def test_corr_matches_known_values():
    # Exact equality is wrong here: the sums-of-squares form lands a few ulps
    # off 1.0 (observed 0.9999999999999998), which is float behaviour, not a
    # defect worth "fixing" in the estimator.
    assert abs(corr([1, 2, 3, 4], [2, 4, 6, 8]) - 1.0) < 1e-12    # perfect +
    assert abs(corr([1, 2, 3, 4], [8, 6, 4, 2]) + 1.0) < 1e-12    # perfect -
    assert corr([1, 2, 3, 4], [5, 5, 5, 5]) == 0.0                # no variance


def test_corr_of_independent_noise_is_near_zero():
    rng = random.Random(7)
    a = [rng.gauss(0, 1) for _ in range(4000)]
    b = [rng.gauss(0, 1) for _ in range(4000)]
    # 4 standard errors is a generous band; anything wider means a real bug.
    assert abs(corr(a, b)) < 4 / math.sqrt(4000)


def test_it_can_actually_detect_a_planted_correlation():
    """The failure mode that matters: a test that never finds anything.

    Plant r≈0.15 - comfortably above the profitable threshold - and require
    the estimate to land near it. If this ever fails, a negative result from
    the real run means nothing.
    """
    rng = random.Random(11)
    a, b = [], []
    for _ in range(4000):
        x = rng.gauss(0, 1)
        a.append(x)
        b.append(0.15 * x + math.sqrt(1 - 0.15 ** 2) * rng.gauss(0, 1))
    assert 0.10 < corr(a, b) < 0.20


def test_returns_are_log_returns_over_consecutive_times():
    series = {1: 100.0, 2: 110.0, 3: 121.0}
    out = returns(series, [1, 2, 3])
    assert len(out) == 2
    assert all(abs(v - math.log(1.1)) < 1e-12 for v in out)


def test_profitable_threshold_clears_break_even():
    """r must be big enough that arcsin(r)/pi covers the house's cut."""
    r = profitable_r()
    accuracy = 0.5 + math.asin(r) / math.pi
    assert abs(accuracy - BREAK_EVEN) < 1e-9
    # Deriv's 1.9233x payout puts this near 0.063; guard the ballpark so a
    # payout typo cannot silently move the bar.
    assert 0.05 < r < 0.08


def test_bonferroni_threshold_rises_with_test_count():
    """More tests must demand more evidence, or multiple comparisons bite."""
    one = bonferroni_z(1)
    many = bonferroni_z(84)
    assert many > one
    assert abs(one - 1.96) < 0.01        # single test = the familiar 1.96
    assert 3.0 < many < 3.6              # 84 tests lands around 3.2
