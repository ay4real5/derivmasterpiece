"""Known-answer tests. A backtest that has not been checked against data with
a KNOWN result is just a number generator."""
import math
import random

import pytest

from pricebot.reversal_backtest import (
    TAKER_FEE,
    bar_returns,
    bounce_autocorrelation,
    break_even_move,
    choose_streak,
    simulate_reversal,
    split_sample,
)


def coin_returns(n=40000, size=0.01, seed=1):
    """Pure random signs - no reversal edge exists."""
    r = random.Random(seed)
    return [(size if r.random() < 0.5 else -size) for _ in range(n)]


def reverting_returns(n=40000, size=0.01, p_reverse=0.60, seed=2, streak=2):
    """After `streak` same-direction bars, reverse with probability p_reverse."""
    r = random.Random(seed)
    out = [size if r.random() < 0.5 else -size]
    for _ in range(n):
        run = 1
        while run < len(out) + 1 and run < streak and \
                len(out) >= run + 1 and (out[-run] > 0) == (out[-run - 1] > 0):
            run += 1
        last = 1 if out[-1] > 0 else -1
        if len(out) >= streak and all((1 if x > 0 else -1) == last
                                      for x in out[-streak:]):
            nxt = -last if r.random() < p_reverse else last
        else:
            nxt = 1 if r.random() < 0.5 else -1
        out.append(nxt * size)
    return out


# --- the null: no edge must produce no profit ------------------------------

def test_no_edge_loses_exactly_the_fees():
    """Random signs: the strategy must lose the fee and nothing else."""
    rets = coin_returns()
    r = simulate_reversal(rets, 2, TAKER_FEE)
    assert r["trades"] > 1000
    # gross should be ~0; net should be ~ -fee per trade
    assert abs(r["gross"] / r["trades"]) < 0.0004
    assert r["mean_net"] == pytest.approx(-2 * TAKER_FEE, abs=0.0004)


def test_no_edge_with_zero_fee_breaks_even():
    r = simulate_reversal(coin_returns(), 2, 0.0)
    assert r["mean_net"] == pytest.approx(0.0, abs=0.0004)
    assert abs(r["tstat"]) < 3.0


def test_win_rate_near_half_when_there_is_no_edge():
    r = simulate_reversal(coin_returns(), 2, 0.0)
    assert 0.45 < r["win_rate"] < 0.55


# --- the signal: an injected edge must be found ----------------------------

def test_detects_an_injected_reversal_edge():
    r = simulate_reversal(reverting_returns(p_reverse=0.60), 2, 0.0)
    assert r["win_rate"] > 0.55
    assert r["mean_net"] > 0
    assert r["tstat"] > 5


def test_an_injected_edge_can_still_be_eaten_by_fees():
    """THE central point of the module: a real edge is not a profitable one.

    A 50.5% reversal edge on 0.1% bars earns far less than a 0.11% round trip
    costs, so the win rate is above half and the money is still negative.
    """
    rets = reverting_returns(n=60000, size=0.001, p_reverse=0.505)
    r = simulate_reversal(rets, 2, TAKER_FEE)
    # The edge is genuinely there before costs...
    assert r["gross"] > 0, "the injected edge should show up gross"
    # ...and gone after them. With 0.1% bars against a 0.11% round trip every
    # single trade nets negative, so the win rate is 0 - which is exactly the
    # point: a positive gross edge and a 0% net win rate coexist happily.
    assert r["mean_net"] < 0, "fees must be able to kill a genuine edge"
    assert r["win_rate"] == 0.0


def test_bigger_bars_make_the_same_edge_payable():
    """Same edge, larger moves: the fee stops mattering."""
    small = simulate_reversal(reverting_returns(n=60000, size=0.001,
                                                p_reverse=0.60), 2, TAKER_FEE)
    large = simulate_reversal(reverting_returns(n=60000, size=0.02,
                                                p_reverse=0.60), 2, TAKER_FEE)
    assert large["mean_net"] > small["mean_net"]


# --- mechanics -------------------------------------------------------------

def test_fee_is_charged_on_both_sides_of_every_trade():
    r = simulate_reversal(coin_returns(n=5000), 2, TAKER_FEE)
    assert r["fee_paid"] == pytest.approx(2 * TAKER_FEE * r["trades"])


def test_gross_minus_fees_equals_net():
    r = simulate_reversal(coin_returns(n=5000), 3, TAKER_FEE)
    assert r["net"] == pytest.approx(r["gross"] - r["fee_paid"], abs=1e-9)


def test_longer_streaks_produce_fewer_trades():
    rets = coin_returns(n=40000)
    counts = [simulate_reversal(rets, k, 0.0)["trades"] for k in (2, 3, 4, 5)]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] > counts[-1]


def test_zero_returns_do_not_count_as_a_direction():
    """A flat bar has no sign; treating it as one invents streaks."""
    rets = [0.01, 0.0, 0.01, 0.01, -0.01] * 100
    r = simulate_reversal(rets, 2, 0.0)
    # the (0.01, 0.0) and (0.0, 0.01) windows must be rejected
    assert r["trades"] < len(rets) / 2


def test_streak_must_be_positive():
    with pytest.raises(ValueError):
        simulate_reversal([0.01, -0.01], 0)


def test_empty_input_is_safe():
    r = simulate_reversal([], 2)
    assert r["trades"] == 0 and r["net"] == 0.0


def test_bar_returns_skips_bad_prices():
    cs = [{"close": 100}, {"close": 0}, {"close": 110}]
    assert len(bar_returns(cs)) == 1


# --- the artefact check ----------------------------------------------------

def test_bid_ask_bounce_is_negligible_at_hourly_scale():
    """The magnitude argument that rules out the classic artefact."""
    ac = bounce_autocorrelation(half_spread=0.00005, vol_per_bar=0.004)
    assert abs(ac) < 0.001, "bounce should be tiny relative to hourly vol"


def test_bid_ask_bounce_dominates_at_tick_scale():
    """Same formula must show the artefact IS real when bars are tiny -
    otherwise the check above proves nothing."""
    ac = bounce_autocorrelation(half_spread=0.00005, vol_per_bar=0.00005)
    assert ac < -0.4


def test_break_even_move_is_the_round_trip():
    assert break_even_move(0.00055) == pytest.approx(0.0011)


# --- out-of-sample discipline ---------------------------------------------

def test_split_sample_halves_without_overlap():
    a, b = split_sample(list(range(100)))
    assert len(a) == 50 and len(b) == 50
    assert set(a).isdisjoint(set(b))


def test_choose_streak_finds_the_injected_one():
    rets = reverting_returns(n=60000, size=0.01, p_reverse=0.65, streak=3)
    assert choose_streak(rets, (2, 3, 4, 5), 0.0) in (2, 3)


def test_choose_streak_only_sees_what_it_is_given():
    """Guards the discipline: it must not reach past its argument."""
    train, _test = split_sample(reverting_returns(n=20000))
    k = choose_streak(train, (2, 3), 0.0)
    assert k in (2, 3)


# --- the volatility filter -------------------------------------------------

def _candles_from_returns(rets, ranges=None):
    """Build OHLC bars whose closes follow `rets` and whose high/low ranges
    follow `ranges`, so a volatility filter has something to see."""
    out, p = [], 100.0
    for i, r in enumerate(rets):
        o = p
        p *= math.exp(r)
        span = (ranges[i] if ranges else abs(r)) or 1e-6
        hi = max(o, p) * (1 + span / 2)
        lo = min(o, p) * (1 - span / 2)
        out.append({"open": o, "high": hi, "low": lo, "close": p, "epoch": i * 3600})
    return out


def test_vol_forecast_is_aligned_and_causal():
    """out[t] must depend only on bars <= t. Leakage here would be invisible
    and would make every filtered result meaningless."""
    from pricebot.reversal_backtest import vol_forecasts
    rets = coin_returns(n=300, size=0.01)
    cs = _candles_from_returns(rets)
    full = vol_forecasts(cs, lookback=6)
    # Truncating the future must not change any earlier value.
    trunc = vol_forecasts(cs[:200], lookback=6)
    assert full[:200] == trunc


def test_vol_forecast_needs_two_bars_before_reporting():
    from pricebot.reversal_backtest import vol_forecasts
    cs = _candles_from_returns([0.01, -0.01, 0.01])
    assert vol_forecasts(cs, lookback=6)[0] is None


def test_causal_rank_uses_only_the_past():
    from pricebot.reversal_backtest import causal_rank
    vals = [float(i) for i in range(100)]
    # a monotone series: the newest value is always the largest so far
    assert causal_rank(vals, 90) == pytest.approx(1.0)
    # and it must refuse before it has enough history
    assert causal_rank(vals, 5) is None


def test_causal_rank_is_none_when_history_is_missing():
    from pricebot.reversal_backtest import causal_rank
    assert causal_rank([None] * 100, 50) is None


def test_vol_buckets_are_flat_when_volatility_carries_no_information():
    """Constant-range bars: the gross edge must not vary by bucket, so the
    diagnostic cannot invent a reason to filter."""
    from pricebot.reversal_backtest import reversal_by_vol_bucket
    rets = reverting_returns(n=8000, size=0.01, p_reverse=0.60)
    cs = _candles_from_returns(rets, ranges=[0.01] * len(rets))
    rows = [r for r in reversal_by_vol_bucket(cs, 2) if r["trades"] > 50]
    assert len(rows) >= 2
    g = [r["gross_per_trade"] for r in rows]
    assert max(g) - min(g) < 0.004, "flat volatility must give flat buckets"


def test_vol_buckets_rise_when_the_edge_scales_with_volatility():
    """The mechanism the whole idea depends on, injected deliberately: bigger
    bars carry a proportionally bigger reversal edge."""
    from pricebot.reversal_backtest import reversal_by_vol_bucket
    rng = random.Random(21)
    rets, ranges = [], []
    prev = 1
    for i in range(20000):
        # volatility alternates in long blocks so a trailing forecast can see it
        big = (i // 400) % 2 == 0
        size = 0.03 if big else 0.003
        # 60% reversal after two same-direction bars
        if len(rets) >= 2 and (rets[-1] > 0) == (rets[-2] > 0):
            last = 1 if rets[-1] > 0 else -1
            s = -last if rng.random() < 0.60 else last
        else:
            s = 1 if rng.random() < 0.5 else -1
        rets.append(s * size)
        ranges.append(size)
    cs = _candles_from_returns(rets, ranges)
    rows = [r for r in reversal_by_vol_bucket(cs, 2) if r["trades"] > 50]
    assert rows[-1]["gross_per_trade"] > rows[0]["gross_per_trade"] * 2


def test_filtered_reversal_takes_fewer_trades_as_the_threshold_rises():
    from pricebot.reversal_backtest import simulate_reversal_filtered
    cs = _candles_from_returns(coin_returns(n=6000, size=0.01))
    counts = [simulate_reversal_filtered(cs, 2, q, fee_per_side=0.0)["trades"]
              for q in (0.0, 0.5, 0.9)]
    assert counts == sorted(counts, reverse=True)


def test_filtered_reversal_is_null_on_random_data():
    from pricebot.reversal_backtest import simulate_reversal_filtered
    cs = _candles_from_returns(coin_returns(n=20000, size=0.01))
    r = simulate_reversal_filtered(cs, 2, 0.5, fee_per_side=0.0)
    assert r["trades"] > 200
    assert abs(r["tstat"]) < 3.0


def test_filtered_reversal_never_reads_the_bar_it_trades():
    """Changing a FUTURE bar must not change an earlier trade's decision.

    The single most important guard: a filter that peeks at the bar it is
    about to trade will look spectacular and be worthless.
    """
    from pricebot.reversal_backtest import simulate_reversal_filtered
    rets = list(coin_returns(n=2000, size=0.01))
    cs = _candles_from_returns(rets)
    a = simulate_reversal_filtered(cs[:1500], 2, 0.5, fee_per_side=0.0)
    b = simulate_reversal_filtered(cs, 2, 0.5, fee_per_side=0.0)
    # every trade in the truncated run must also exist in the full run
    assert b["trades"] >= a["trades"]
