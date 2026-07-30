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


# --- single instance -------------------------------------------------------

def test_live_pid_holds_the_lock():
    """Two supervisors means double trades AND two independent daily caps, so
    the cap you configured is silently doubled. This actually happened."""
    from tools.risefall_supervisor import stale_lock
    assert stale_lock("4321", lambda pid: True) is False


def test_dead_pid_releases_the_lock():
    """A crash must not become a permanent outage."""
    from tools.risefall_supervisor import stale_lock
    assert stale_lock("4321", lambda pid: False) is True


def test_unreadable_lock_is_treated_as_stale():
    from tools.risefall_supervisor import stale_lock
    for junk in ("", "   ", "not-a-pid", "12.5"):
        assert stale_lock(junk, lambda pid: True) is True


def test_our_own_pid_never_blocks_us():
    import os
    from tools.risefall_supervisor import stale_lock
    assert stale_lock(str(os.getpid()), lambda pid: True) is True


def test_pid_alive_says_no_for_nonsense_pids():
    from tools.risefall_supervisor import _pid_alive
    assert _pid_alive(0) is False
    assert _pid_alive(-5) is False


def test_pid_alive_finds_this_process():
    import os
    from tools.risefall_supervisor import _pid_alive
    assert _pid_alive(os.getpid()) is True


def test_child_is_launched_without_a_console():
    """STATUS_CONTROL_C_EXIT killed a session one second after it opened
    because console-control events reach every process sharing a console."""
    from tools.risefall_supervisor import CREATE_NO_WINDOW
    assert CREATE_NO_WINDOW == 0x08000000


# --- logging must survive having no console --------------------------------

def test_log_writes_to_the_file_even_with_no_usable_stream(tmp_path):
    """Under pythonw there is no stdout, which silently hid the lock-refusal
    message - the task fired, exited cleanly, and recorded nothing."""
    from tools.risefall_supervisor import log

    class Dead:
        def write(self, *a): raise OSError("no console")
        def flush(self): raise OSError("no console")

    p = tmp_path / "out.log"
    log("hello", stream=Dead(), path=str(p))
    assert "hello" in p.read_text(encoding="utf-8")


def test_log_does_not_raise_when_the_path_is_unwritable():
    """Logging must never be the thing that kills the supervisor."""
    from tools.risefall_supervisor import log
    import io
    buf = io.StringIO()
    log("still fine", stream=buf, path="Z:/nope/nowhere/out.log")
    assert "still fine" in buf.getvalue()


def test_log_appends_rather_than_truncating(tmp_path):
    from tools.risefall_supervisor import log
    import io
    p = tmp_path / "out.log"
    log("first", stream=io.StringIO(), path=str(p))
    log("second", stream=io.StringIO(), path=str(p))
    text = p.read_text(encoding="utf-8")
    assert "first" in text and "second" in text
