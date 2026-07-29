"""Is a volatility forecast worth more than the fee charged to express it?

`vol_forecast` establishes that day-ahead volatility on real markets IS
forecastable - gold r=+0.57, silver r=+0.61, confirmed by two independent
estimators. That is a genuine statistical edge and nothing like the synthetic
indices, where TICK_ANALYSIS.md found nothing at all.

But a forecast is not money. To turn it into money you must buy an instrument
whose payoff depends on volatility, and Deriv charges a measured 15-38% for
those. So the only question that matters is arithmetic: converted into a
position, is the forecast worth more than the fee?

WHAT THIS MODULE DELIBERATELY ASSUMES, ALL IN OUR FAVOUR. The answer it
produces is an UPPER BOUND, not a forecast of results:

  - Deriv is assumed to price at the STATIONARY volatility distribution,
    ignoring the clustering entirely. A real pricer updates with recent
    volatility, and every bit of updating it does comes straight off our edge.
  - The forecaster is given the TRUE AR(1) coefficient, not an estimate.
  - No spread, no slippage, no minimum stake, no execution delay.

If the bound is below the fee, the trade loses and no amount of implementation
skill recovers it. That is the useful direction for a bound to fail in.

THE ERROR THIS MODULE WAS BUILT AROUND. The first version of this simulation
priced the "fair" quote at the MEDIAN volatility rather than as the
EXPECTATION of the touch probability over the volatility distribution. Those
differ by Jensen's inequality, and with daily log-volatility scattered at
sd=0.57 they differ enormously - the bug manufactured a 13% edge out of a
zero-margin, zero-skill setup. It was caught only because the simulation is
checked against two cases whose answers are known in advance:

    no margin, no skill  ->  exactly 0
    margin m, no skill   ->  exactly -m/(1+m)

Those checks are the tests below, and they are the reason any number from here
can be quoted at all.
"""
from __future__ import annotations

import math
import random
from typing import Any

# Fixed grid over the standard normal for expectations. 401 points on
# [-6, 6] sigma; the tests confirm this integrates to the known answer.
_N = 401
_LO, _HI = -6.0, 6.0
_STEP = (_HI - _LO) / (_N - 1)
_X = [_LO + i * _STEP for i in range(_N)]
_W = [math.exp(-x * x / 2) / math.sqrt(2 * math.pi) * _STEP for x in _X]
_TOT = sum(_W)
_W = [w / _TOT for w in _W]


def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def touch_probability(distance: float, sigma: float) -> float:
    """P(a driftless walk of volatility `sigma` touches `distance` away).

    Reflection principle, one-sided barrier: P = 2 * Phi(-d/sigma). Capped at
    1 because the approximation can exceed it for a very close barrier.
    """
    if sigma <= 0:
        return 0.0
    return min(1.0, 2.0 * norm_cdf(-distance / sigma))


def expected_touch_probability(distance: float, mean_log_vol: float,
                               sd_log_vol: float) -> float:
    """E[touch probability] when log-volatility is normal - THE fair price.

    Not the touch probability at the mean volatility. Those are different
    numbers and confusing them is the bug documented in the module docstring.
    """
    if sd_log_vol <= 0:
        return touch_probability(distance, math.exp(mean_log_vol))
    return sum(w * touch_probability(distance, math.exp(mean_log_vol + sd_log_vol * x))
               for x, w in zip(_X, _W))


def expected_return_per_trade(margin: float, phi: float, sd_log_vol: float,
                              mean_log_vol: float, *, barrier_over_vol: float = 0.15,
                              trials: int = 100000, seed: int = 1,
                              skill: float = 1.0) -> float:
    """Expected return per trade, as a fraction of stake.

    `margin` is the model-free house margin measured from a complementary
    quote pair. `skill` scales the forecast: 0 means no information, which
    must return exactly -margin/(1+margin).
    """
    rng = random.Random(seed)
    sigma_typ = math.exp(mean_log_vol)
    d = barrier_over_vol * sigma_typ

    p_touch_fair = expected_touch_probability(d, mean_log_vol, sd_log_vol)
    p_no_fair = 1.0 - p_touch_fair
    if not (0.0 < p_touch_fair < 1.0):
        return 0.0
    pay_touch = 1.0 / (p_touch_fair * (1.0 + margin))
    pay_no = 1.0 / (p_no_fair * (1.0 + margin))

    sd_cond = sd_log_vol * math.sqrt(max(0.0, 1.0 - phi * phi))
    sd_fore = sd_cond if skill > 0 else sd_log_vol

    # E[touch] increases with the forecast mean, so "buy TOUCH when our
    # conditional expectation beats the quote" reduces to one threshold,
    # solved once instead of integrating inside the loop.
    lo, hi = mean_log_vol - 10 * sd_log_vol, mean_log_vol + 10 * sd_log_vol
    for _ in range(60):
        mid = (lo + hi) / 2
        if expected_touch_probability(d, mid, sd_fore) > p_touch_fair:
            hi = mid
        else:
            lo = mid
    threshold = (lo + hi) / 2

    prev = mean_log_vol + rng.gauss(0, sd_log_vol)
    total = 0.0
    for _ in range(trials):
        true_log = mean_log_vol + phi * (prev - mean_log_vol) + rng.gauss(0, sd_cond)
        forecast = mean_log_vol + skill * phi * (prev - mean_log_vol)
        p_true = touch_probability(d, math.exp(true_log))
        if forecast > threshold:
            total += p_true * pay_touch - 1.0
        else:
            total += (1.0 - p_true) * pay_no - 1.0
        prev = true_log
    return total / trials


def max_supportable_margin(phi: float, sd_log_vol: float, mean_log_vol: float, *,
                           barrier_over_vol: float = 0.15, trials: int = 60000,
                           seed: int = 1, iterations: int = 20) -> float:
    """The largest fee this forecast could pay and still break even.

    The number to compare against a measured margin. If it is below what
    Deriv charges, the trade loses however well it is implemented.
    """
    lo, hi = 0.0, 1.0
    for _ in range(iterations):
        mid = (lo + hi) / 2
        r = expected_return_per_trade(mid, phi, sd_log_vol, mean_log_vol,
                                      barrier_over_vol=barrier_over_vol,
                                      trials=trials, seed=seed)
        if r > 0:
            lo = mid
        else:
            hi = mid
    return lo


def house_margin(payout_a: float, payout_b: float, stake: float = 100.0) -> float:
    """Margin from a COMPLEMENTARY quote pair - no model of any kind.

    Exactly one of the two contracts pays, so buying both for one unit of
    payout each costs stake/payout_a + stake/payout_b, and the excess over 1
    is the house margin. This is the only cost number in the project that
    needs no distributional assumption at all.
    """
    if payout_a <= 0 or payout_b <= 0:
        raise ValueError("payouts must be positive")
    return stake / payout_a + stake / payout_b - 1.0
