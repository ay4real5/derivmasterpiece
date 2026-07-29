"""Known-answer tests: each function must be null on data with no effect and
must find an effect that was deliberately put there."""
import math
import random

import pytest

from pricebot.vol_forecast import (
    block_vols,
    close_to_close_vol,
    forecast_value,
    horizon_days,
    parkinson_vol,
    persistence,
    split_half,
    vol_series,
)


def candle(hi, lo, close=None, open_=None):
    return {"high": hi, "low": lo, "close": close if close is not None else (hi + lo) / 2,
            "open": open_ if open_ is not None else (hi + lo) / 2}


# --- Parkinson estimator ---------------------------------------------------

def test_parkinson_recovers_known_volatility():
    """Simulate days of known sigma and check the estimator returns it.

    The whole power argument rests on this estimator being unbiased, so it is
    checked against a simulation rather than assumed from the formula.
    """
    rng = random.Random(7)
    sigma = 0.02
    steps = 500
    ests = []
    for _ in range(400):
        p = 1.0
        hi = lo = p
        for _ in range(steps):
            p *= math.exp(rng.gauss(0, sigma / math.sqrt(steps)))
            hi, lo = max(hi, p), min(lo, p)
        ests.append(parkinson_vol(candle(hi, lo)))
    mean = sum(ests) / len(ests)
    assert sigma * 0.9 < mean < sigma * 1.1


def test_parkinson_rejects_degenerate_candles():
    assert parkinson_vol(candle(1.0, 1.0)) is None      # flat: stale feed
    assert parkinson_vol(candle(1.0, 2.0)) is None      # high below low
    assert parkinson_vol(candle(-1.0, -2.0)) is None
    assert parkinson_vol({"high": "x", "low": 1}) is None


def test_parkinson_is_more_efficient_than_close_to_close():
    """The reason this estimator was chosen: it must actually be less noisy."""
    rng = random.Random(8)
    sigma, steps = 0.02, 200
    park, ctc = [], []
    prev_close = 1.0
    for _ in range(600):
        p = prev_close
        hi = lo = p
        for _ in range(steps):
            p *= math.exp(rng.gauss(0, sigma / math.sqrt(steps)))
            hi, lo = max(hi, p), min(lo, p)
        park.append(parkinson_vol(candle(hi, lo, close=p)))
        ctc.append(abs(math.log(p / prev_close)))
        prev_close = p
    cv_park = statistics_cv(park)
    cv_ctc = statistics_cv(ctc)
    assert cv_park < cv_ctc * 0.6      # expected roughly 5x variance ratio


def statistics_cv(xs):
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var) / m


def test_vol_series_drops_bad_candles_without_shifting_others():
    cs = [candle(2, 1), candle(1, 1), candle(4, 2)]
    out = vol_series(cs)
    assert len(out) == 2
    assert out[0] == pytest.approx(parkinson_vol(candle(2, 1)))


# --- persistence -----------------------------------------------------------

def _gbm_days(n, vols, seed=3, steps=100):
    """Days whose TRUE volatility follows `vols`, so persistence is known."""
    rng = random.Random(seed)
    out = []
    p = 100.0
    for s in vols[:n]:
        hi = lo = p
        for _ in range(steps):
            p *= math.exp(rng.gauss(0, s / math.sqrt(steps)))
            hi, lo = max(hi, p), min(lo, p)
        out.append(candle(hi, lo, close=p))
    return out


def test_persistence_null_when_volatility_is_constant():
    """Constant true sigma: measured persistence must not be significant."""
    vols = [0.02] * 4000
    days = _gbm_days(4000, vols, seed=4)
    out = persistence(vol_series(days))
    assert out["p"] > 0.01
    assert abs(out["r"]) < 0.06


def test_persistence_null_when_volatility_is_random_each_day():
    """Volatility varies but with NO memory - the key control.

    This is the case that separates 'volatility moves around' from
    'volatility is forecastable'. A test that fires here is useless.
    """
    rng = random.Random(5)
    vols = [math.exp(rng.gauss(math.log(0.02), 0.4)) for _ in range(4000)]
    days = _gbm_days(4000, vols, seed=6)
    out = persistence(vol_series(days))
    assert out["p"] > 0.01


def test_persistence_detects_a_real_garch_like_process():
    """Log volatility as an AR(1) - the standard model of clustering."""
    rng = random.Random(9)
    lv, vols = math.log(0.02), []
    for _ in range(4000):
        lv = 0.95 * lv + 0.05 * math.log(0.02) + rng.gauss(0, 0.15)
        vols.append(math.exp(lv))
    days = _gbm_days(4000, vols, seed=10)
    out = persistence(vol_series(days))
    assert out["p"] < 1e-10
    assert out["r"] > 0.3


def test_persistence_uses_logs_so_one_spike_cannot_create_it():
    """A single huge outlier must not manufacture persistence."""
    rng = random.Random(11)
    vols = [0.02] * 2000
    vols[1000] = 5.0                       # one crisis day
    days = _gbm_days(2000, vols, seed=12)
    out = persistence(vol_series(days))
    assert out["p"] > 0.01


# --- split-half ------------------------------------------------------------

def test_split_half_consistent_for_a_real_effect():
    rng = random.Random(13)
    lv, vols = math.log(0.02), []
    for _ in range(4000):
        lv = 0.95 * lv + 0.05 * math.log(0.02) + rng.gauss(0, 0.15)
        vols.append(math.exp(lv))
    out = split_half(vol_series(_gbm_days(4000, vols, seed=14)))
    assert out["ok"] and out["consistent"]


def test_split_half_catches_drift_masquerading_as_persistence():
    """A one-way trend in volatility gives a big correlation that is NOT a
    day-ahead forecast. This is the exact error made earlier on gold."""
    vols = [0.005 + 0.00002 * i for i in range(2000)]   # monotone drift
    out = split_half(vol_series(_gbm_days(2000, vols, seed=15)))
    assert out["ok"]
    # the halves must not both show a strong effect the way a real one would
    assert not (out["first"]["r"] > 0.3 and out["second"]["r"] > 0.3)


def test_split_half_refuses_tiny_samples_rather_than_guessing():
    assert split_half([0.01] * 20)["ok"] is False


# --- horizon and blocks ----------------------------------------------------

@pytest.mark.parametrize("s,days", [("1d", 1), ("7d", 7), ("24h", 1),
                                    ("15m", 15 / 1440), ("1t", 1 / 86400)])
def test_horizon_days(s, days):
    assert horizon_days(s) == pytest.approx(days)


def test_horizon_days_unknown_is_infinite_not_zero():
    """An unparseable duration must look UNtradeable, not instantaneous."""
    assert horizon_days(None) == float("inf")
    assert horizon_days("banana") == float("inf")
    assert horizon_days("xyz") == float("inf")


def test_block_vols_are_non_overlapping():
    out = block_vols([1, 2, 3, 4, 5, 6, 7], 3)
    assert out == [2.0, 5.0]              # (1+2+3)/3, (4+5+6)/3; 7 dropped


def test_block_vols_do_not_manufacture_autocorrelation():
    """Blocking white noise must not create persistence."""
    rng = random.Random(16)
    noise = [math.exp(rng.gauss(0, 1)) for _ in range(6000)]
    out = persistence(block_vols(noise, 7))
    assert out["p"] > 0.01


def test_block_of_one_is_identity():
    assert block_vols([1.0, 2.0], 1) == [1.0, 2.0]


# --- economics -------------------------------------------------------------

def test_forecast_value_is_r_squared():
    v = forecast_value(0.3)
    assert v["variance_explained"] == pytest.approx(0.09)
    assert v["explained_pct"] == pytest.approx(9.0)


def test_forecast_value_sign_does_not_change_the_worth():
    assert (forecast_value(-0.4)["variance_explained"]
            == pytest.approx(forecast_value(0.4)["variance_explained"]))


def test_close_to_close_matches_manual():
    cs = [candle(2, 1, close=100), candle(2, 1, close=110)]
    assert close_to_close_vol(cs)[0] == pytest.approx(abs(math.log(1.1)))
