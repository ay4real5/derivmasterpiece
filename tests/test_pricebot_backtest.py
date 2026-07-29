import pytest

from pricebot.backtest import Result, _resolve, compare, simulate
from pricebot.signals import NeverTrade, Signal, Strategy


def flat_candles(n, price=100.0):
    return [{"open": price, "high": price, "low": price, "close": price}
            for _ in range(n)]


def noisy_candles(n, price=100.0, seed=3, step=0.0005):
    import random
    r = random.Random(seed)
    out = []
    for _ in range(n):
        nxt = price * (1 + r.gauss(0, step))
        out.append({"open": price, "high": max(price, nxt) * 1.0002,
                    "low": min(price, nxt) * 0.9998, "close": nxt})
        price = nxt
    return out


class AlwaysLong(Strategy):
    name = "always_long"

    def evaluate(self, candles):
        return Signal(1, 0.001, 600, 1.0, "always long")


class Peeker(Strategy):
    """Records how many candles it was shown, to prove no look-ahead."""
    name = "peeker"

    def __init__(self):
        self.max_seen = 0

    def evaluate(self, candles):
        self.max_seen = max(self.max_seen, len(candles))
        return None


# --- the honesty guarantees --------------------------------------------

def test_strategy_never_sees_beyond_the_current_bar():
    """A single off-by-one here manufactures an edge from nothing."""
    candles = noisy_candles(700)
    p = Peeker()
    simulate(candles, p, vol_window=500)
    assert p.max_seen <= len(candles) - 1


def test_same_candle_ambiguity_resolves_against_the_strategy():
    """When one candle spans both brackets, OHLC cannot say which came
    first. Assuming the good one wins is how backtests lie."""
    candles = [{"open": 100, "high": 100, "low": 100, "close": 100},
               {"open": 100, "high": 110, "low": 90, "close": 100}]  # spans both
    outcome, price, idx = _resolve(candles, 0, 1, tp_price=105, sl_price=95,
                                   max_bars=5)
    assert outcome == "stop_loss"


def test_resolve_finds_take_profit_when_only_it_is_touched():
    candles = [{"open": 100, "high": 100, "low": 100, "close": 100},
               {"open": 100, "high": 106, "low": 99, "close": 105}]
    outcome, price, _ = _resolve(candles, 0, 1, 105, 95, 5)
    assert outcome == "take_profit"
    assert price == 105


def test_resolve_handles_a_short_position():
    candles = [{"open": 100, "high": 100, "low": 100, "close": 100},
               {"open": 100, "high": 101, "low": 94, "close": 95}]
    outcome, _, _ = _resolve(candles, 0, -1, tp_price=95, sl_price=103,
                             max_bars=5)
    assert outcome == "take_profit"


def test_unresolved_when_neither_bracket_is_reached():
    candles = flat_candles(10)
    outcome, _, _ = _resolve(candles, 0, 1, 105, 95, max_bars=5)
    assert outcome == "unresolved"


# --- the baseline -------------------------------------------------------

def test_never_produces_no_trades_and_zero_result():
    r = simulate(noisy_candles(700), NeverTrade(), vol_window=500)
    assert r.count == 0
    assert r.net == 0.0
    assert r.commission == 0.0


def test_a_trading_strategy_pays_commission_on_every_position():
    r = simulate(noisy_candles(900), AlwaysLong(), vol_window=500,
                 commission=0.73)
    assert r.count > 0
    assert r.commission == pytest.approx(0.73 * r.count)
    assert r.net == pytest.approx(r.gross - r.commission)


def test_commission_is_what_separates_gross_from_net():
    r = simulate(noisy_candles(900), AlwaysLong(), vol_window=500,
                 commission=5.0)
    assert r.net < r.gross


# --- mechanics ----------------------------------------------------------

def test_only_one_position_is_held_at_a_time():
    r = simulate(noisy_candles(900), AlwaysLong(), vol_window=500)
    for a, b in zip(r.trades, r.trades[1:]):
        assert b.index > a.exit_index


def test_too_few_candles_produces_nothing_rather_than_guessing():
    assert simulate(flat_candles(10), AlwaysLong(), vol_window=500).count == 0


def test_a_motionless_series_never_trades():
    # zero volatility means no measurable target distance
    assert simulate(flat_candles(700), AlwaysLong(), vol_window=500).count == 0


def test_compare_runs_every_strategy_over_the_same_candles():
    candles = noisy_candles(900)
    out = compare(candles, {"never": NeverTrade(), "always": AlwaysLong()},
                  vol_window=500)
    assert out["never"].net == 0.0
    assert out["always"].count > 0


def test_result_stats_are_consistent():
    r = simulate(noisy_candles(900), AlwaysLong(), vol_window=500)
    assert 0.0 <= r.win_rate <= 1.0
    assert r.wins <= r.count
