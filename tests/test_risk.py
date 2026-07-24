from datetime import date

from deriv_bot.risk import RiskLimits, RiskManager


def test_stops_on_max_daily_loss():
    rm = RiskManager(RiskLimits(max_daily_loss=10, max_consecutive_losses=100, max_trades=1000))
    rm.record_trade(-6)
    assert rm.can_trade()
    rm.record_trade(-5)
    assert not rm.can_trade()
    assert "daily loss" in rm.stop_reason


def test_stops_on_consecutive_losses():
    rm = RiskManager(RiskLimits(max_daily_loss=1000, max_consecutive_losses=3, max_trades=1000))
    rm.record_trade(-1)
    rm.record_trade(1)  # a win resets the streak
    rm.record_trade(-1)
    rm.record_trade(-1)
    assert rm.can_trade()
    rm.record_trade(-1)
    assert not rm.can_trade()
    assert "consecutive losses" in rm.stop_reason


def test_stops_on_max_trades():
    rm = RiskManager(RiskLimits(max_daily_loss=1000, max_consecutive_losses=1000, max_trades=3))
    for _ in range(3):
        rm.record_trade(0.5)
    assert not rm.can_trade()
    assert "max trade count" in rm.stop_reason


def test_stays_open_under_all_limits():
    rm = RiskManager(RiskLimits(max_daily_loss=100, max_consecutive_losses=10, max_trades=10))
    rm.record_trade(-1)
    rm.record_trade(2)
    assert rm.can_trade()
    assert rm.stop_reason is None


def test_stops_on_target_profit():
    rm = RiskManager(RiskLimits(
        max_daily_loss=50, max_consecutive_losses=100, max_trades=1000, target_profit=10,
    ))
    rm.record_trade(6)
    assert rm.can_trade()
    rm.record_trade(5)
    assert not rm.can_trade()
    assert "profit target" in rm.stop_reason


def test_no_target_profit_by_default():
    rm = RiskManager(RiskLimits(max_daily_loss=50, max_consecutive_losses=100, max_trades=1000))
    rm.record_trade(1000.0)
    assert rm.can_trade()


def test_counters_reset_on_new_utc_day():
    rm = RiskManager(RiskLimits(max_daily_loss=10, max_consecutive_losses=100, max_trades=1000))
    rm._today = lambda: date(2024, 1, 1)
    rm._day = date(2024, 1, 1)
    rm.record_trade(-6)
    assert rm.trade_count == 1
    assert rm.daily_pnl == -6

    rm._today = lambda: date(2024, 1, 2)
    assert rm.can_trade()
    assert rm.trade_count == 0
    assert rm.daily_pnl == 0.0
    assert rm.stop_reason is None


def test_stopped_bot_resumes_after_day_rolls_over():
    rm = RiskManager(RiskLimits(max_daily_loss=10, max_consecutive_losses=100, max_trades=1000))
    rm._today = lambda: date(2024, 1, 1)
    rm._day = date(2024, 1, 1)
    rm.record_trade(-6)
    rm.record_trade(-5)
    assert not rm.can_trade()

    rm._today = lambda: date(2024, 1, 2)
    assert rm.can_trade()
