import math

import pytest

from pricebot.symbol_cost import (
    SECONDS_PER_YEAR,
    cost_per_day,
    expected_seconds_per_trade,
    rank,
    realised_vol,
    stop_out_distance,
    trades_per_day,
)


def walk(n, sigma_per_step, start=100.0, seed=7):
    """Deterministic pseudo-random walk with a known per-step volatility."""
    import random
    r = random.Random(seed)
    price, out = start, []
    for _ in range(n):
        price *= math.exp(r.gauss(0, sigma_per_step))
        out.append({"close": price})
    return out


# --- volatility estimator, checked against a known answer ---------------

def test_realised_vol_recovers_a_known_volatility():
    """The free known-answer test: build a walk with a chosen sigma and check
    the estimator finds it. Without this the whole ranking rests on an
    unverified number."""
    target_annual = 0.10
    per_min = target_annual / math.sqrt(SECONDS_PER_YEAR / 60)
    got = realised_vol(walk(20000, per_min), seconds_per_candle=60)
    assert got == pytest.approx(target_annual, rel=0.05)


def test_realised_vol_scales_with_the_candle_period():
    # same returns, longer candles -> higher annualised vol
    data = walk(5000, 0.0001)
    assert realised_vol(data, 60) > realised_vol(data, 3600)


def test_realised_vol_is_zero_without_enough_data():
    assert realised_vol([], 60) == 0.0
    assert realised_vol([{"close": 100}], 60) == 0.0


def test_realised_vol_skips_unusable_candles():
    data = [{"close": 100}, {"close": 0}, {"close": "x"}, {}, {"close": 101},
            {"close": 102}]
    assert realised_vol(data, 60) > 0


# --- the cost model -----------------------------------------------------

def test_stop_out_distance_is_the_inverse_multiplier():
    assert stop_out_distance(400) == pytest.approx(0.0025)   # 0.25%
    assert stop_out_distance(100) == pytest.approx(0.01)     # 1.00%
    with pytest.raises(ValueError):
        stop_out_distance(0)


def test_first_passage_time_grows_with_the_square_of_distance():
    t1 = expected_seconds_per_trade(0.01, 0.10)
    t2 = expected_seconds_per_trade(0.02, 0.10)
    assert t2 == pytest.approx(4 * t1)      # doubling d quadruples the time


def test_first_passage_time_falls_with_the_square_of_volatility():
    t1 = expected_seconds_per_trade(0.01, 0.10)
    t2 = expected_seconds_per_trade(0.01, 0.20)
    assert t2 == pytest.approx(t1 / 4)


def test_a_motionless_symbol_never_resolves():
    assert expected_seconds_per_trade(0.01, 0.0) == float("inf")
    assert trades_per_day(0.01, 0.0) == 0.0


# --- the ranking reproduces what was measured live ----------------------

MEASURED = [
    {"symbol": "frxEURUSD", "commission": 1.00, "multiplier": 100, "vol": 0.039},
    {"symbol": "frxXAUUSD", "commission": 0.60, "multiplier": 100, "vol": 0.15},
    {"symbol": "R_10",      "commission": 0.73, "multiplier": 400, "vol": 0.101},
    {"symbol": "R_100",     "commission": 1.82, "multiplier": 100, "vol": 0.966},
    {"symbol": "R_50",      "commission": 1.96, "multiplier": 200, "vol": 0.50},
]


def test_ranking_matches_the_live_measurement():
    order = [r["symbol"] for r in rank(MEASURED)]
    assert order[0] == "frxEURUSD"
    assert order[1] == "frxXAUUSD"
    assert order[-1] == "R_50"


def test_cheapest_commission_is_not_the_cheapest_symbol():
    """Gold has the lowest commission of the five and still costs about twice
    EUR/USD per day, because it moves four times as fast. Ranking on
    commission alone gets the answer wrong."""
    ranked = {r["symbol"]: r for r in rank(MEASURED)}
    assert ranked["frxXAUUSD"]["commission"] < ranked["frxEURUSD"]["commission"]
    assert ranked["frxXAUUSD"]["cost_per_day"] > ranked["frxEURUSD"]["cost_per_day"]


def test_leverage_enters_the_cost_squared():
    base = {"symbol": "X", "commission": 1.0, "vol": 0.10}
    low = rank([{**base, "multiplier": 100}])[0]["cost_per_day"]
    high = rank([{**base, "multiplier": 400}])[0]["cost_per_day"]
    assert high == pytest.approx(16 * low)     # 4x leverage -> 16x cost


def test_unpriceable_symbols_are_reported_not_dropped():
    ranked = rank([
        {"symbol": "good", "commission": 1.0, "multiplier": 100, "vol": 0.1},
        {"symbol": "bad", "commission": None, "multiplier": 100, "vol": 0.1},
    ])
    assert len(ranked) == 2
    assert ranked[-1]["symbol"] == "bad"
    assert ranked[-1]["unavailable"]


def test_cost_per_day_uses_measured_volatility_not_assumed():
    """A worked example of why the volatility has to be measured.

    With EUR/USD assumed at a textbook 8%, this returns 0.18/day. With the
    3.9% actually measured from candles it returns 0.042 - four times
    cheaper, because cost scales with vol SQUARED and (0.039/0.08)^2 = 0.24.
    The assumption would not have changed EUR/USD's rank, but on a symbol
    nearer a rival it would have.
    """
    assumed = cost_per_day(1.00, stop_out_distance(100), 0.08)
    measured = cost_per_day(1.00, stop_out_distance(100), 0.039)
    assert assumed == pytest.approx(0.18, abs=0.02)
    assert measured == pytest.approx(0.042, abs=0.01)
    assert measured / assumed == pytest.approx((0.039 / 0.08) ** 2, rel=0.01)
