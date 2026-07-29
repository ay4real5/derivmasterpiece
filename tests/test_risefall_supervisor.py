"""The budget decision and the day boundary, tested at their edges.

The digit bot's daily cap failed at exactly these boundaries - a restarting
child reset its own PnL and the day ran past the limit - so the equivalent
logic here is pure and checked rather than trusted.
"""
from datetime import datetime, timezone

import pytest

from tools.risefall_supervisor import seconds_until_next_utc_day, verdict


# --- the loss cap ----------------------------------------------------------

def test_trades_while_inside_the_budget():
    assert verdict(-10.0, 100.0, 0.0) == "trade"
    assert verdict(+50.0, 100.0, 0.0) == "trade"


def test_stops_exactly_at_the_cap_not_one_trade_past_it():
    """Inclusive on purpose: 'not worse than' is what a limit means."""
    assert verdict(-100.0, 100.0, 0.0) == "loss-cap"


def test_stops_beyond_the_cap():
    assert verdict(-100.01, 100.0, 0.0) == "loss-cap"


def test_cap_is_read_as_a_magnitude_so_a_positive_config_still_works():
    """-100 and 100 must mean the same limit; a sign slip here disables it."""
    assert verdict(-150.0, -100.0, 0.0) == "loss-cap"


def test_zero_cap_disables_the_loss_limit():
    assert verdict(-99999.0, 0.0, 0.0) == "trade"


# --- the profit target -----------------------------------------------------

def test_stops_at_the_target():
    assert verdict(100.0, 500.0, 100.0) == "target"
    assert verdict(100.01, 500.0, 100.0) == "target"


def test_below_the_target_keeps_trading():
    assert verdict(99.99, 500.0, 100.0) == "trade"


def test_zero_target_disables_the_profit_stop():
    assert verdict(999999.0, 500.0, 0.0) == "trade"


def test_loss_cap_wins_when_both_could_somehow_apply():
    """Not reachable with sane config, but the order must be deterministic."""
    assert verdict(-500.0, 100.0, -1000.0) == "loss-cap"


# --- the UTC day boundary --------------------------------------------------

def test_seconds_until_next_day_at_midnight_is_a_full_day():
    t = datetime(2026, 7, 29, 0, 0, 0, tzinfo=timezone.utc)
    assert seconds_until_next_utc_day(t) == pytest.approx(86400.0)


def test_seconds_until_next_day_just_before_midnight_is_small():
    t = datetime(2026, 7, 29, 23, 59, 30, tzinfo=timezone.utc)
    assert seconds_until_next_utc_day(t) == pytest.approx(30.0)


def test_seconds_until_next_day_is_never_negative_or_zero():
    for hour in range(24):
        t = datetime(2026, 7, 29, hour, 17, 3, tzinfo=timezone.utc)
        s = seconds_until_next_utc_day(t)
        assert 0 < s <= 86400


# --- the crash-loop guard --------------------------------------------------

def test_backoff_is_bounded_and_gives_up():
    """A crash loop is a bug; the supervisor must not spin on it forever."""
    from tools.risefall_supervisor import BACKOFF_SECONDS, MAX_CONSECUTIVE_FAILURES
    assert MAX_CONSECUTIVE_FAILURES > 0
    assert all(s > 0 for s in BACKOFF_SECONDS)
    assert list(BACKOFF_SECONDS) == sorted(BACKOFF_SECONDS), "backoff must grow"
