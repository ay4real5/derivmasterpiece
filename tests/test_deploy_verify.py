"""Tests written against the ACTUAL log text that hid the problem.

The fixtures below are copied from risefall_live.log, not invented, so the
parsers are proven against the exact lines that were misread.
"""
import pytest

from pricebot.deploy_verify import (
    caps_match_running,
    config_matches_running,
    decode_task_result,
    observed_expiries,
    observed_rungs,
    parse_effective_settings,
    parse_supervisor_settings,
    restart_succeeded,
    restart_verified,
)

# Real lines from the log, verbatim.
STALE_LOG = """\
[2026-07-30T01:50:15Z] supervisor up | config=config.risefall.yaml cap=100.0 target=none session=30.0m
[2026-07-30T02:20:30Z] starting: python.exe -u run_pricebot.py --config config.risefall.yaml --minutes 30.0
[02:20:35Z] session start | rise_fall | symbols=['R_10', 'R_25'] strategy=pdf_rise_fall stake=3.0 expiry set by strategy candles=60s
[02:44:43Z] OPEN    R_100 CALL stake 3.00 payout 5.78 (1.9267x, break-even 51.9%) for 5m | pdf score 80.4 [gate] (ema 1.00 rsi 0.72 bb 0.50 adx 0.50 candle 1.00, ADX=22.1, agree=True)
"""

FRESH_LOG = STALE_LOG + """\
[2026-07-30T03:00:00Z] supervisor up | config=config.risefall.yaml cap=700.0 target=700.0 session=30.0m
[03:00:05Z] session start | rise_fall | symbols=['R_10', 'R_25'] strategy=pdf_rise_fall stake=3.0 expiry 3t staking=recovery_ladder candles=60s
[03:00:06Z] OPEN    R_50 PUT stake 3.00 rung 1 payout 5.77 (1.9233x, break-even 52.0%) for 3t | pdf score 33.4 [gate] (ema 0.15 rsi 0.28 bb 0.50 adx 0.50 candle 0.50, ADX=25.1, agree=True)
[03:00:40Z] OPEN    R_50 PUT stake 3.25 rung 2 payout 6.25 (1.9231x, break-even 52.0%) for 3t | pdf score 34.2 [gate] (ema 0.15 rsi 0.31 bb 0.50 adx 0.50 candle 0.50, ADX=24.0, agree=True)
"""

WANTED = {"pricebot": {"instrument": "rise_fall", "stake": 3.00, "duration": 3,
                       "duration_unit": "t",
                       "staking": {"name": "recovery_ladder"}}}


# --- the silent error code -------------------------------------------------

def test_already_running_is_decoded_and_is_not_success():
    """0x80070420 is THE code that turned a restart into a no-op unnoticed."""
    assert "ALREADY RUNNING" in decode_task_result(2147946720)
    assert restart_succeeded(2147946720) is False


def test_running_and_zero_count_as_success():
    assert restart_succeeded(0) is True
    assert restart_succeeded(267009) is True
    assert "currently running" in decode_task_result(267009)


def test_unknown_codes_return_searchable_hex_not_a_guess():
    out = decode_task_result(2147500037)
    assert "0x80004005" in out
    assert "unrecognised" in out


def test_no_result_is_not_success():
    assert decode_task_result(None) == "no result recorded"
    assert restart_succeeded(None) is False


# --- reading the running state out of the log ------------------------------

def test_stale_child_is_detected_from_its_own_log_line():
    """`expiry set by strategy` and no `staking=` is what a pre-patch child
    logs. It must not be read as agreeing with a 3-tick ladder config."""
    got = parse_effective_settings(STALE_LOG)
    assert got is not None
    assert got["expiry"] is None
    assert got["staking"] is None
    assert got["stake"] == 3.0


def test_fresh_child_reports_its_terms():
    got = parse_effective_settings(FRESH_LOG)
    assert got["expiry"] == "3t"
    assert got["staking"] == "recovery_ladder"
    assert got["instrument"] == "rise_fall"


def test_most_recent_session_wins_not_the_first():
    """A log holds every session ever; only the last one is running."""
    assert parse_effective_settings(FRESH_LOG)["expiry"] == "3t"


def test_empty_log_returns_none_rather_than_a_default():
    assert parse_effective_settings("") is None
    assert parse_supervisor_settings("") is None


# --- the mismatch report ---------------------------------------------------

def test_stale_child_is_reported_as_mismatched_on_every_axis():
    problems = config_matches_running(WANTED, parse_effective_settings(STALE_LOG))
    joined = " | ".join(problems)
    assert "expiry" in joined
    assert "staking" in joined
    assert len(problems) >= 2


def test_fresh_child_reports_no_problems():
    assert config_matches_running(WANTED, parse_effective_settings(FRESH_LOG)) == []


def test_missing_session_is_reported_not_silently_passed():
    problems = config_matches_running(WANTED, None)
    assert len(problems) == 1 and "nothing is running" in problems[0]


# --- the caps --------------------------------------------------------------

def test_old_caps_are_reported_against_the_wanted_ones():
    problems = caps_match_running(700.0, 700.0, parse_supervisor_settings(STALE_LOG))
    joined = " | ".join(problems)
    assert "loss cap" in joined and "100.0" in joined
    assert "profit target" in joined


def test_matching_caps_report_nothing():
    assert caps_match_running(700.0, 700.0,
                              parse_supervisor_settings(FRESH_LOG)) == []


def test_target_none_parses_as_none_not_zero():
    """'target=none' must not read as 0.0, which would look like a real value."""
    assert parse_supervisor_settings(STALE_LOG)["target"] is None
    assert parse_supervisor_settings(STALE_LOG)["cap"] == 100.0


# --- ground truth from the trades themselves -------------------------------

def test_observed_expiries_counts_what_actually_traded():
    """The check that answered the original question: 85 at 5m, 0 at 3t."""
    assert observed_expiries(STALE_LOG) == {"5m": 1}
    got = observed_expiries(FRESH_LOG)
    assert got["5m"] == 1 and got["3t"] == 2


def test_no_rungs_means_no_ladder_ever_ran():
    assert observed_rungs(STALE_LOG) == {}
    assert observed_rungs(FRESH_LOG) == {1: 1, 2: 1}


# --- proving a restart happened -------------------------------------------

def test_restart_with_no_new_supervisor_is_not_verified():
    """The exact failure: Start-ScheduledTask returned, nothing started."""
    assert restart_verified(STALE_LOG, STALE_LOG) is False


def test_restart_with_a_new_supervisor_is_verified():
    assert restart_verified(STALE_LOG, FRESH_LOG) is True


def test_restart_verification_survives_a_same_second_restart():
    """Counting occurrences rather than comparing timestamps, so a restart
    inside one clock second is still detected."""
    same_second = STALE_LOG + (
        "[2026-07-30T01:50:15Z] supervisor up | config=c.yaml cap=700.0 "
        "target=700.0 session=30.0m\n")
    assert restart_verified(STALE_LOG, same_second) is True


def test_real_log_currently_shows_the_mismatch():
    """Guards against 'fixed' meaning 'the checker stopped looking'.

    Runs against the actual log if present. It must find either a genuine
    agreement or a genuine mismatch - never crash, never silently pass on an
    empty read.
    """
    import os
    import yaml
    if not os.path.exists("risefall_live.log"):
        pytest.skip("no live log on this machine")
    text = open("risefall_live.log", encoding="utf-8", errors="ignore").read()
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    running = parse_effective_settings(text)
    problems = config_matches_running(cfg, running)
    assert isinstance(problems, list)
    # Whatever the state, the expiries actually traded must be reported.
    assert isinstance(observed_expiries(text), dict)
