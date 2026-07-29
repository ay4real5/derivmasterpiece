"""Every test validated against data with a KNOWN answer.

A statistical test nobody has checked is an opinion with a p-value attached.
Each function is shown to (a) return null on clean white noise and (b) detect
a deliberately injected pattern of the kind it is meant to find.
"""
import math
import random

import pytest

from pricebot.tick_stats import (
    _chi2_sf,
    autocorrelation,
    cross_correlation,
    hourly_direction,
    hourly_volatility,
    ljung_box,
    returns,
    runs_test,
    streak_continuation,
    summarise,
    volatility_clustering,
)


def white_noise(n=20000, seed=1):
    r = random.Random(seed)
    return [r.gauss(0, 1) for _ in range(n)]


def ar1(n=20000, phi=0.3, seed=2):
    """Deliberately autocorrelated: x_t = phi*x_{t-1} + noise."""
    r = random.Random(seed)
    out, prev = [], 0.0
    for _ in range(n):
        prev = phi * prev + r.gauss(0, 1)
        out.append(prev)
    return out


def signs(series):
    return [1 if v > 0 else (-1 if v < 0 else 0) for v in series]


# --- the chi-square tail, against textbook values -----------------------

def test_chi2_matches_known_critical_values():
    assert _chi2_sf(3.841, 1) == pytest.approx(0.05, abs=0.002)
    assert _chi2_sf(31.410, 20) == pytest.approx(0.05, abs=0.003)
    assert _chi2_sf(37.566, 20) == pytest.approx(0.01, abs=0.002)


# --- autocorrelation ----------------------------------------------------

def test_autocorrelation_is_null_on_white_noise():
    out = autocorrelation(white_noise(), 1)
    assert abs(out["r"]) < 0.03
    assert out["p"] > 0.01


def test_autocorrelation_detects_an_ar1_process():
    """If it cannot find phi=0.3 in 20,000 points it cannot be trusted to
    report an absence either."""
    out = autocorrelation(ar1(phi=0.3), 1)
    assert out["r"] > 0.2
    assert out["p"] < 1e-10


def test_autocorrelation_handles_short_and_flat_input():
    assert autocorrelation([1, 2], 5)["p"] == 1.0
    assert autocorrelation([5.0] * 100, 1)["r"] == 0.0


# --- Ljung-Box ----------------------------------------------------------

def test_ljung_box_is_null_on_white_noise():
    assert ljung_box(white_noise(), 20)["p"] > 0.01


def test_ljung_box_detects_autocorrelation():
    assert ljung_box(ar1(phi=0.3), 20)["p"] < 1e-10


# --- streaks ------------------------------------------------------------

def test_streak_continuation_is_50pct_on_white_noise():
    d = signs(white_noise(50000))
    for k in (2, 3, 4):
        out = streak_continuation(d, k)
        assert out["n"] > 100
        assert abs(out["p_continue"] - 0.5) < 0.03


def test_streak_continuation_detects_injected_momentum():
    """A series where an up-move makes the next up-move likelier."""
    r = random.Random(7)
    d, prev = [], 1
    for _ in range(50000):
        cont = r.random() < 0.60          # 60% continuation
        prev = prev if cont else -prev
        d.append(prev)
    out = streak_continuation(d, 2)
    assert out["p_continue"] > 0.55
    assert out["p"] < 1e-10


def test_streak_rejects_a_bad_streak_length():
    with pytest.raises(ValueError):
        streak_continuation([1, -1], 0)


# --- runs test ----------------------------------------------------------

def test_runs_test_is_null_on_white_noise():
    assert runs_test(signs(white_noise()))["p"] > 0.01


def test_runs_test_detects_perfect_alternation():
    alternating = [1 if i % 2 == 0 else -1 for i in range(5000)]
    out = runs_test(alternating)
    assert out["p"] < 1e-10
    assert out["runs"] > out["expected"]


def test_runs_test_needs_enough_data():
    assert runs_test([1, -1, 1])["p"] == 1.0


# --- volatility clustering ---------------------------------------------

def test_volatility_clustering_is_null_on_constant_scale_noise():
    assert volatility_clustering(white_noise(), 1)["p"] > 0.01


def test_volatility_clustering_detects_a_garch_like_series():
    """Large moves following large moves - the real-market signature."""
    r = random.Random(5)
    out, vol = [], 1.0
    for _ in range(20000):
        vol = 0.9 * vol + 0.1 * abs(r.gauss(0, 1)) * 2
        out.append(r.gauss(0, vol))
    assert volatility_clustering(out, 1)["p"] < 1e-6


# --- cross-correlation --------------------------------------------------

def test_cross_correlation_is_null_between_independent_series():
    assert cross_correlation(white_noise(seed=1), white_noise(seed=2), 0)["p"] > 0.01


def test_cross_correlation_detects_a_lead_lag_relationship():
    a = white_noise(20000, seed=3)
    b = [0.0] + [0.7 * v for v in a[:-1]]      # b follows a by one step
    out = cross_correlation(a, b, lag=1)
    assert abs(out["r"]) > 0.5
    assert out["p"] < 1e-10


# --- the multiple-comparisons guard ------------------------------------

def test_summarise_calls_noise_noise():
    """100 tests at p<0.01 yield ~1 hit on pure noise. That must not read as
    a discovery."""
    res = [{"p": 0.005}] + [{"p": 0.5}] * 99
    out = summarise(res, alpha=0.01)
    assert out["hits"] == 1
    assert out["expected_by_chance"] == pytest.approx(1.0)
    assert out["survivors"] == []
    assert "consistent with pure noise" in out["verdict"]


def test_summarise_promotes_a_result_that_survives_bonferroni():
    res = [{"p": 1e-9}] + [{"p": 0.5}] * 99
    out = summarise(res, alpha=0.01)
    assert len(out["survivors"]) == 1
    assert "survive Bonferroni" in out["verdict"]


def test_summarise_handles_no_tests():
    assert summarise([])["tests"] == 0


# --- returns ------------------------------------------------------------

def test_returns_skips_nonpositive_prices():
    assert len(returns([100, 0, 101, 102])) == 1


# --- time-of-day -----------------------------------------------------------

def test_hourly_direction_null_on_uniform_series():
    """A generator with no clock should show no hour-of-day direction effect."""
    r = random.Random(11)
    epochs = list(range(0, 86400 * 2))
    dirs = [1 if r.random() < 0.5 else -1 for _ in epochs]
    out = hourly_direction(epochs, dirs)
    assert out["df"] == 23
    assert out["p"] > 0.01


def test_hourly_direction_detects_injected_hour_effect():
    """Bias hours 8-11 upward; the test must find it."""
    r = random.Random(12)
    epochs = list(range(0, 86400 * 2))
    dirs = []
    for e in epochs:
        h = (e // 3600) % 24
        p_up = 0.60 if 8 <= h <= 11 else 0.5
        dirs.append(1 if r.random() < p_up else -1)
    out = hourly_direction(epochs, dirs)
    assert out["p"] < 1e-6
    hot = [h["up_rate"] for h in out["hours"] if 8 <= h["hour"] <= 11]
    assert min(hot) > 0.55


def test_hourly_direction_ignores_zero_moves():
    """Flat ticks carry no direction and must not enter the counts."""
    epochs = list(range(0, 86400))
    dirs = [0] * len(epochs)
    out = hourly_direction(epochs, dirs)
    assert out["n"] == 0
    assert out["p"] == 1.0


def test_hourly_volatility_null_on_constant_scale():
    r = random.Random(13)
    epochs = list(range(0, 86400 * 2))
    rets = [r.gauss(0, 1) for _ in epochs]
    out = hourly_volatility(epochs, rets)
    assert out["k"] == 24
    assert out["p"] > 0.01


def test_hourly_volatility_detects_scheduled_session():
    """Triple the scale during hours 13-16 - a 'session' the test must see."""
    r = random.Random(14)
    epochs = list(range(0, 86400 * 2))
    rets = []
    for e in epochs:
        h = (e // 3600) % 24
        rets.append(r.gauss(0, 3.0 if 13 <= h <= 16 else 1.0))
    out = hourly_volatility(epochs, rets)
    assert out["p"] < 1e-10
    loud = [x["sd"] for x in out["hours"] if 13 <= x["hour"] <= 16]
    quiet = [x["sd"] for x in out["hours"] if not 13 <= x["hour"] <= 16]
    assert min(loud) > 2 * max(quiet)
