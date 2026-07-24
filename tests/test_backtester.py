from deriv_bot.backtester import backtest_over_prices, last_digit
from deriv_bot.strategy import Signal, Strategy


class _AlwaysOverStrategy(Strategy):
    def __init__(self, barrier: str = "4"):
        self.barrier = barrier

    def on_tick(self, digit):
        return Signal("DIGITOVER", self.barrier, "test")


def test_signal_resolves_against_next_tick_not_current():
    # digits: 1, 9 -- resolving DIGITOVER(4) against the SAME digit that
    # fired the signal (1, which is <=4) would lose; resolving against the
    # NEXT digit (9, which is >4), as a real duration=1-tick contract does,
    # wins.
    prices = [123.11, 123.19]
    report, trades = backtest_over_prices(prices, stake=1.0, strategy=_AlwaysOverStrategy())
    assert report["num_trades"] == 1
    assert trades[0]["digit"] == 9
    assert trades[0]["won"] is True


def test_no_trade_on_final_tick_with_no_next_digit():
    prices = [123.11]
    report, trades = backtest_over_prices(prices, stake=1.0, strategy=_AlwaysOverStrategy())
    assert report["num_trades"] == 0


def test_last_digit_recovers_trailing_zero():
    # JSON floats drop trailing zeros: a 531.70 quote arrives as 531.7.
    # Naive str()[-1] reads 7; pip-aware formatting must read 0.
    assert last_digit(531.7, 2) == 0
    assert last_digit(531.79, 2) == 9
    assert last_digit(500.0, 2) == 0
    assert last_digit(9.5, 3) == 0


def test_match_and_differ_resolution():
    from deriv_bot.backtester import _resolves_win

    assert _resolves_win(Signal("DIGITMATCH", "7", "t"), 7) is True
    assert _resolves_win(Signal("DIGITMATCH", "7", "t"), 3) is False
    assert _resolves_win(Signal("DIGITDIFF", "7", "t"), 3) is True
    assert _resolves_win(Signal("DIGITDIFF", "7", "t"), 7) is False


def test_match_differ_theoretical_win_prob():
    from deriv_bot.edge import theoretical_win_prob

    assert theoretical_win_prob("DIGITMATCH", "5") == 0.1
    assert theoretical_win_prob("DIGITDIFF", "5") == 0.9


def test_digitover_zero_loses_when_next_digit_is_zero():
    # 123.20 arrives as float 123.2 — its true last digit is 0, so
    # DIGITOVER 0 must LOSE on it. This is the regression the trailing-zero
    # bug hid: digit 0 never appeared, so DIGITOVER 0 never lost.
    prices = [123.11, 123.2]
    report, trades = backtest_over_prices(
        prices, stake=1.0, strategy=_AlwaysOverStrategy(barrier="0"),
    )
    assert report["num_trades"] == 1
    assert trades[0]["digit"] == 0
    assert trades[0]["won"] is False
