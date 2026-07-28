import csv
import time
from datetime import date, datetime, timezone

import pytest

from tools.supervisor import (
    StallWatchdog,
    budget_verdict,
    classify_stop,
    day_pnl,
    is_stalled,
    last_trade_time,
    seconds_until_next_utc_day,
)


def write_journal(path, rows):
    fields = ["timestamp", "symbol", "contract_type", "barrier",
              "stake", "payout", "profit", "balance_after", "reason"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return str(path)


def test_day_pnl_sums_only_the_requested_utc_day(tmp_path):
    p = write_journal(tmp_path / "j.csv", [
        {"timestamp": "2026-07-26T23:59:00+00:00", "profit": "-500"},
        {"timestamp": "2026-07-27T01:00:00+00:00", "profit": "-128"},
        {"timestamp": "2026-07-27T02:00:00+00:00", "profit": "+99.13"},
        {"timestamp": "2026-07-28T00:00:01+00:00", "profit": "-999"},
    ])
    assert day_pnl(p, date(2026, 7, 27)) == pytest.approx(-28.87)
    assert day_pnl(p, date(2026, 7, 26)) == pytest.approx(-500)


def test_day_pnl_skips_unsettled_and_malformed_rows(tmp_path):
    p = write_journal(tmp_path / "j.csv", [
        {"timestamp": "2026-07-27T01:00:00+00:00", "profit": ""},        # dry-run
        {"timestamp": "2026-07-27T01:01:00+00:00", "profit": "not-a-number"},
        {"timestamp": "2026-07-27T01:02:00+00:00", "profit": "-10"},
    ])
    assert day_pnl(p, date(2026, 7, 27)) == pytest.approx(-10)


def test_day_pnl_missing_file_is_zero_not_an_error(tmp_path):
    assert day_pnl(str(tmp_path / "nope.csv"), date(2026, 7, 27)) == 0.0


def test_budget_verdict_blocks_restart_past_the_daily_loss():
    # the whole point: a fresh process would restart daily_pnl at 0.0
    assert budget_verdict(-1000.0, 1000.0, 250.0) is not None
    assert budget_verdict(-1200.0, 1000.0, 250.0) is not None


def test_budget_verdict_blocks_restart_past_the_profit_target():
    assert budget_verdict(250.0, 1000.0, 250.0) is not None


def test_budget_verdict_allows_restart_inside_the_budget():
    # a crash mid-day with budget left should come straight back up
    assert budget_verdict(-793.27, 1000.0, 250.0) is None
    assert budget_verdict(0.0, 1000.0, 250.0) is None
    assert budget_verdict(249.0, 1000.0, 250.0) is None


def test_budget_verdict_without_a_profit_target():
    assert budget_verdict(10_000.0, 1000.0, None) is None
    assert budget_verdict(-1000.0, 1000.0, None) is not None


def test_classify_stop_recognises_the_day_scoped_limits():
    # the exact strings RiskManager._check_limits emits
    assert classify_stop("profit target reached (+250.78)") == "target"
    assert classify_stop("max daily loss reached (-1000.00)") == "daily_loss"


def test_classify_stop_treats_per_process_limits_as_transient():
    assert classify_stop("20 consecutive losses") == "transient"
    assert classify_stop("max trade count reached (100000)") == "transient"


def test_classify_stop_handles_no_reason():
    # a crash prints no stop line at all — restart normally
    assert classify_stop(None) is None
    assert classify_stop("") is None
    assert classify_stop("websocket closed unexpectedly") is None


def test_log_resolves_its_stream_at_call_time(monkeypatch, capsys):
    # regression: log(stream=sys.stdout) as a DEFAULT bound None at import
    # under pythonw.exe, so every call raised AttributeError and the task
    # died within a second of starting.
    import io

    import tools.supervisor as sup

    monkeypatch.setattr(sup.sys, "stdout", None)
    sup.log("must not raise when stdout is None")  # no exception

    buf = io.StringIO()
    monkeypatch.setattr(sup.sys, "stdout", buf)
    sup.log("hello")
    assert "hello" in buf.getvalue()
    assert buf.getvalue().startswith("[supervisor ")


def test_seconds_until_next_utc_day():
    now = datetime(2026, 7, 27, 23, 0, 0, tzinfo=timezone.utc)
    assert seconds_until_next_utc_day(now) == pytest.approx(3600.0)
    now = datetime(2026, 7, 27, 0, 0, 0, tzinfo=timezone.utc)
    assert seconds_until_next_utc_day(now) == pytest.approx(86400.0)


def test_last_trade_time_returns_the_newest_settled_row(tmp_path):
    p = write_journal(tmp_path / "j.csv", [
        {"timestamp": "2026-07-28T01:00:00+00:00", "profit": "-2"},
        {"timestamp": "2026-07-28T01:05:00+00:00", "profit": "3"},
    ])
    assert last_trade_time(p) == datetime(2026, 7, 28, 1, 5, tzinfo=timezone.utc)


def test_last_trade_time_ignores_unsettled_rows(tmp_path):
    p = write_journal(tmp_path / "j.csv", [
        {"timestamp": "2026-07-28T01:00:00+00:00", "profit": "-2"},
        {"timestamp": "2026-07-28T01:05:00+00:00", "profit": ""},   # dry-run
    ])
    assert last_trade_time(p) == datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)


def test_last_trade_time_on_a_missing_file():
    assert last_trade_time("nope.csv") is None


def test_is_stalled_detects_a_long_silence(tmp_path):
    # the phase-independent check: neither MAX_EMPTY_SCANS nor
    # SETTLEMENT_TIMEOUT covers a drop during the buy or balance call
    p = write_journal(tmp_path / "j.csv", [
        {"timestamp": "2026-07-28T01:00:00+00:00", "profit": "-2"},
    ])
    now = datetime(2026, 7, 28, 1, 6, tzinfo=timezone.utc)  # 360s later
    stalled, age = is_stalled(p, stall_seconds=300, now=now)
    assert stalled
    assert age == pytest.approx(360)


def test_is_stalled_tolerates_a_normal_gap(tmp_path):
    # real gaps reach ~94s; the threshold must not turn those into outages
    p = write_journal(tmp_path / "j.csv", [
        {"timestamp": "2026-07-28T01:00:00+00:00", "profit": "-2"},
    ])
    now = datetime(2026, 7, 28, 1, 1, 34, tzinfo=timezone.utc)  # 94s
    stalled, _ = is_stalled(p, stall_seconds=300, now=now)
    assert not stalled


def test_is_stalled_is_false_before_any_trade(tmp_path):
    # a fresh journal is not evidence of a stall
    p = write_journal(tmp_path / "j.csv", [])
    stalled, age = is_stalled(p, stall_seconds=300)
    assert not stalled
    assert age == 0.0


def test_watchdog_fires_and_calls_back(tmp_path):
    p = write_journal(tmp_path / "j.csv", [
        {"timestamp": "2020-01-01T00:00:00+00:00", "profit": "-2"},  # ancient
    ])
    seen = []
    wd = StallWatchdog(p, stall_seconds=1, on_stall=seen.append, poll_seconds=0.05)
    wd.start()
    deadline = time.monotonic() + 3
    while not seen and time.monotonic() < deadline:
        time.sleep(0.02)
    wd.stop()
    assert wd.fired
    assert seen and seen[0] > 0


def test_watchdog_stays_quiet_while_trading(tmp_path):
    p = str(tmp_path / "j.csv")
    seen = []
    wd = StallWatchdog(p, stall_seconds=300, on_stall=seen.append, poll_seconds=0.05)
    # rewrite a fresh trade on every poll, as a live bot would
    write_journal(p, [{"timestamp": datetime.now(timezone.utc).isoformat(), "profit": "1"}])
    wd.start()
    time.sleep(0.4)
    wd.stop()
    assert not wd.fired
    assert seen == []


def test_day_target_is_waived_when_on_target_is_continue():
    """Regression: on_target=continue applied only to the child's stop
    reason, while this day-level check halted anyway — so "keep running back
    to back" silently stopped for 15h after the day's PnL passed +1000."""
    # on_target == "stop": the day target halts
    assert budget_verdict(1058.85, 1000.0, 1000.0) is not None
    # on_target == "continue": caller passes None for the target
    assert budget_verdict(1058.85, 1000.0, None) is None


def test_daily_loss_is_never_waived_by_on_target():
    # the loss stop is the only real protection and must survive every mode
    assert budget_verdict(-1000.0, 1000.0, None) is not None
    assert budget_verdict(-1500.0, 1000.0, None) is not None
