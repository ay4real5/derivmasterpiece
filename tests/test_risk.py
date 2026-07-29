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


def test_opening_daily_pnl_reduces_the_available_budget():
    """The cap did not cap. RiskManager starts at zero in every new process
    and the supervisor restarts on any crash, so each restart granted a fresh
    max_daily_loss. Observed live: relaunched with the day at -521 'within
    limits', and the day ran to -1016.38 against a 1000 cap."""
    r = RiskManager(RiskLimits(max_daily_loss=1000, max_consecutive_losses=99,
                               max_trades=99999), opening_daily_pnl=-900.0)
    assert r.can_trade()
    r.record_trade(-99.0)          # day now -999
    assert r.can_trade()
    r.record_trade(-2.0)           # day now -1001, past the cap
    assert not r.can_trade()
    assert "max daily loss" in r.stop_reason


def test_an_offset_already_past_the_cap_stops_before_the_first_trade():
    r = RiskManager(RiskLimits(max_daily_loss=1000, max_consecutive_losses=99,
                               max_trades=99999), opening_daily_pnl=-1000.0)
    assert not r.can_trade()


def test_no_offset_behaves_exactly_as_before():
    r = RiskManager(RiskLimits(max_daily_loss=1000, max_consecutive_losses=99,
                               max_trades=99999))
    assert r.daily_pnl == 0.0
    assert r.can_trade()


def test_a_positive_offset_carries_profit_toward_the_target():
    r = RiskManager(RiskLimits(max_daily_loss=1000, max_consecutive_losses=99,
                               max_trades=99999, target_profit=300),
                    opening_daily_pnl=250.0)
    assert r.can_trade()
    r.record_trade(60.0)           # day now +310
    assert not r.can_trade()
    assert "profit target" in r.stop_reason


def test_the_utc_rollover_clears_a_carried_offset(monkeypatch):
    # the offset belongs to the day it was measured for; a new day is clean
    from datetime import date as _date
    r = RiskManager(RiskLimits(max_daily_loss=1000, max_consecutive_losses=99,
                               max_trades=99999), opening_daily_pnl=-990.0)
    monkeypatch.setattr(RiskManager, "_today", staticmethod(lambda: _date(2099, 1, 1)))
    assert r.can_trade()            # rollover detected
    assert r.daily_pnl == 0.0
