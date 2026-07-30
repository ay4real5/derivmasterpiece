"""The ladder rung must survive a child restart.

Spotted from the Deriv Positions screen: two consecutive losses, both at 3.00.
The journal and the supervisor log together explain it:

    22:50:08  R_25  CALL       3.00  -3.00
    22:52:15Z exited with code 3
    22:52:16Z ran 3967s, exit=3; restarting in 5s
    22:52:34  R_10  DIGITEVEN  3.00  -3.00   <-- should have been 3.25
    22:53:18  R_25  CALL       3.25  -3.25   <-- ladder restarted from scratch

`RecoveryLadder.consecutive_losses` lives in process memory, so every restart
dropped the rung. Exit code 3 is the dead-socket guard, which fires on any
dropped websocket - routine, not rare. The whole climb was paid for and the
rung that would have recovered it was never placed.

Same class of bug as the daily cap resetting on restart, which was fixed by
reconstructing the day's PnL from the journal. The ladder now uses the same
source.
"""
import csv
from datetime import date

import pytest

from deriv_bot.staking import DoublingMartingale, RecoveryLadder
from tools.supervisor import trailing_loss_streak

SEQ = [3, 3.25, 6.77, 14.10, 29.36, 61.13, 127.29, 265.05, 265.05]


def journal(tmp_path, profits, day="2026-07-30", name="j.csv"):
    """profits: list of floats, or None for an unsettled/dry-run row."""
    p = tmp_path / name
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["timestamp", "profit"])
        w.writeheader()
        for i, v in enumerate(profits):
            w.writerow({"timestamp": f"{day}T{i // 60:02d}:{i % 60:02d}:00+00:00",
                        "profit": "" if v is None else v})
    return str(p)


DAY = date(2026, 7, 30)


# --- reconstructing the streak from the journal ----------------------------

def test_a_journal_ending_in_two_losses_returns_two(tmp_path):
    j = journal(tmp_path, [5.0, -3.0, -3.25])
    assert trailing_loss_streak(j, DAY) == 2


def test_a_journal_ending_in_a_win_returns_zero(tmp_path):
    j = journal(tmp_path, [-3.0, -3.25, 6.25])
    assert trailing_loss_streak(j, DAY) == 0


def test_only_the_TRAILING_losses_count(tmp_path):
    """An earlier losing run that a win already ended is finished business."""
    j = journal(tmp_path, [-3.0, -3.25, -6.77, 14.0, -3.0])
    assert trailing_loss_streak(j, DAY) == 1


def test_a_missing_or_empty_journal_returns_zero(tmp_path):
    assert trailing_loss_streak(str(tmp_path / "nope.csv"), DAY) == 0
    assert trailing_loss_streak(journal(tmp_path, []), DAY) == 0


def test_unsettled_rows_are_skipped_not_counted_as_wins(tmp_path):
    """A blank profit is a dry-run or in-flight row. Treating it as a win
    would silently end the streak and reset the ladder."""
    j = journal(tmp_path, [-3.0, None, -3.25])
    assert trailing_loss_streak(j, DAY) == 2


def test_a_half_written_final_line_does_not_crash(tmp_path):
    p = tmp_path / "j.csv"
    p.write_text("timestamp,profit\n2026-07-30T00:00:00+00:00,-3.0\n"
                 "2026-07-30T00:01:00+00:00,not-a-number\n", encoding="utf-8")
    assert trailing_loss_streak(str(p), DAY) == 1


def test_other_days_are_ignored(tmp_path):
    j = journal(tmp_path, [-3.0, -3.0], day="2026-07-29")
    assert trailing_loss_streak(j, DAY) == 0


def test_a_zero_profit_ends_the_streak(tmp_path):
    """`RecoveryLadder.record` resets on `profit >= 0`, so this must match or
    the two would disagree about where the cycle stands."""
    j = journal(tmp_path, [-3.0, 0.0])
    assert trailing_loss_streak(j, DAY) == 0


def test_the_day_reset_marker_is_respected(tmp_path):
    """Must use the same cut-off as day_pnl, or the rung and the budget would
    disagree about which trades are 'today'."""
    j = journal(tmp_path, [-3.0, -3.25, -6.77])
    since = "2026-07-30T00:01:00+00:00"
    assert trailing_loss_streak(j, DAY, since=since) == 1


# --- the ladder resuming ---------------------------------------------------

@pytest.mark.parametrize("streak,expected", list(enumerate(SEQ)))
def test_start_streak_opens_at_the_matching_rung(streak, expected):
    s = RecoveryLadder(sequence=SEQ, reset_after_losses=9, start_streak=streak)
    assert s.stake_for(3.0, 0.9233, 10_000.0) == pytest.approx(expected)


def test_zero_streak_is_the_unchanged_default():
    a = RecoveryLadder(sequence=SEQ, reset_after_losses=9)
    b = RecoveryLadder(sequence=SEQ, reset_after_losses=9, start_streak=0)
    assert a.stake_for(3.0, 0.9233, 1e4) == b.stake_for(3.0, 0.9233, 1e4)


def test_a_resumed_ladder_still_resets_on_a_win():
    s = RecoveryLadder(sequence=SEQ, reset_after_losses=9, start_streak=4)
    assert s.stake_for(3.0, 0.9233, 1e4) == pytest.approx(29.36)
    s.record(+27.0)
    assert s.stake_for(3.0, 0.9233, 1e4) == pytest.approx(3.0)


def test_a_resumed_ladder_keeps_climbing():
    s = RecoveryLadder(sequence=SEQ, reset_after_losses=9, start_streak=2)
    assert s.stake_for(3.0, 0.9233, 1e4) == pytest.approx(6.77)
    s.record(-6.77)
    assert s.stake_for(3.0, 0.9233, 1e4) == pytest.approx(14.10)


def test_negative_start_streak_is_rejected():
    with pytest.raises(ValueError, match="start_streak"):
        RecoveryLadder(sequence=SEQ, start_streak=-1)


def test_a_streak_past_the_end_never_exceeds_the_top_rung():
    """A bad read must not invent a stake bigger than the ladder allows."""
    for k in (9, 12, 500):
        s = RecoveryLadder(sequence=SEQ, reset_after_losses=9, start_streak=k)
        assert s.stake_for(3.0, 0.9233, 1e4) <= max(SEQ) + 0.005


def test_doubling_martingale_still_resumes_as_it_always_did():
    s = DoublingMartingale(multiplier=2.0, start_streak=3)
    assert s.stake_for(3.0, 0.9233, 1e4) == pytest.approx(24.0)


# --- the clamp main.py applies ---------------------------------------------

def clamp(streak, sequence, reset):
    """Mirrors main.py so the rule is testable without a live session."""
    ceiling = (len(sequence) - 1) if sequence else None
    if reset:
        ceiling = min(ceiling, reset - 1) if ceiling is not None else reset - 1
    return min(streak, ceiling) if ceiling is not None else streak


def test_the_clamp_stops_at_the_last_rung():
    assert clamp(50, SEQ, 9) == 8
    assert clamp(3, SEQ, 9) == 3


def test_the_clamp_respects_a_short_reset(tmp_path):
    """reset_after_losses shorter than the sequence must win."""
    assert clamp(8, SEQ, 3) == 2


# --- the exact live sequence, end to end -----------------------------------

def test_the_observed_restart_now_continues_the_ladder(tmp_path):
    """Loss at 3.00, restart, next stake must be 3.25 - not 3.00."""
    j = journal(tmp_path, [2.86, -3.00])          # a win, then the 3.00 loss
    streak = trailing_loss_streak(j, DAY)
    assert streak == 1

    seeded = clamp(streak, SEQ, 9)
    resumed = RecoveryLadder(sequence=SEQ, reset_after_losses=9,
                             start_streak=seeded)
    assert resumed.stake_for(3.0, 0.9233, 1e4) == pytest.approx(3.25), (
        "a restart mid-ladder must resume the rung, not restart the cycle")


def test_a_deep_run_resumes_where_it_left_off(tmp_path):
    j = journal(tmp_path, [5.0, -3.0, -3.25, -6.77, -14.10])
    streak = trailing_loss_streak(j, DAY)
    assert streak == 4
    s = RecoveryLadder(sequence=SEQ, reset_after_losses=9,
                       start_streak=clamp(streak, SEQ, 9))
    assert s.stake_for(3.0, 0.9233, 1e4) == pytest.approx(29.36)


def test_the_supervisor_passes_the_flag():
    """Guards against the fix existing but never reaching the child - the
    signature/body mismatch on daily_pnl_offset once crash-looped every run."""
    import inspect

    import main
    from tools import supervisor
    assert "ladder_streak" in inspect.signature(supervisor.run_once).parameters
    src = inspect.getsource(supervisor.run_once)
    assert "--ladder-streak" in src
    for name in ("cmd_scan_trade", "_run_scan_trade"):
        assert "ladder_streak" in inspect.signature(getattr(main, name)).parameters
