import pytest

from pricebot.rise_fall_backtest import break_even_win_rate, simulate_rise_fall
from pricebot.signals import NeverTrade, Signal, Strategy


def c(price, hi=None, lo=None):
    return {"open": price, "high": hi or price + 1, "low": lo or price - 1,
            "close": price}


class AlwaysRise(Strategy):
    name = "always_rise"

    def evaluate(self, candles):
        return Signal(1, 0.001, 300, 1.0, "always")


class AlwaysFall(AlwaysRise):
    def evaluate(self, candles):
        return Signal(-1, 0.001, 300, 1.0, "always")


def test_break_even_matches_the_quoted_payouts():
    """The numbers the PDF's '>= 62%' claim has to clear."""
    assert break_even_win_rate(1.9231) == pytest.approx(0.520, abs=0.001)
    assert break_even_win_rate(1.78) == pytest.approx(0.562, abs=0.001)
    with pytest.raises(ValueError):
        break_even_win_rate(1.0)


def test_a_rising_series_wins_every_rise_contract():
    candles = [c(100 + i) for i in range(200)]
    r = simulate_rise_fall(candles, AlwaysRise(), warmup=60, duration_bars=5)
    assert r.count > 0
    assert r.win_rate == 1.0
    assert r.net > 0


def test_a_rising_series_loses_every_fall_contract():
    candles = [c(100 + i) for i in range(200)]
    r = simulate_rise_fall(candles, AlwaysFall(), warmup=60, duration_bars=5)
    assert r.win_rate == 0.0
    assert r.net < 0


def test_an_exact_tie_counts_as_a_loss():
    # flat series: close at expiry equals entry exactly
    candles = [c(100.0) for _ in range(200)]
    r = simulate_rise_fall(candles, AlwaysRise(), warmup=60, duration_bars=5)
    assert r.count > 0
    assert r.wins == 0


def test_never_places_no_trades():
    candles = [c(100 + i) for i in range(200)]
    r = simulate_rise_fall(candles, NeverTrade(), warmup=60)
    assert r.count == 0
    assert r.net == 0.0
    assert r.skipped_neutral == r.evaluated


def test_payout_multiple_sets_the_reward():
    candles = [c(100 + i) for i in range(200)]
    a = simulate_rise_fall(candles, AlwaysRise(), warmup=60, stake=10,
                           payout_multiple=1.9231)
    b = simulate_rise_fall(candles, AlwaysRise(), warmup=60, stake=10,
                           payout_multiple=1.5)
    assert a.net > b.net


def test_positions_do_not_overlap():
    candles = [c(100 + (i % 7)) for i in range(300)]
    r = simulate_rise_fall(candles, AlwaysRise(), warmup=60, duration_bars=5)
    for x, y in zip(r.trades, r.trades[1:]):
        assert y.index > x.exit_index


def test_too_little_history_produces_nothing():
    assert simulate_rise_fall([c(100)] * 10, AlwaysRise(), warmup=60).count == 0
