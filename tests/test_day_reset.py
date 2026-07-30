"""The day-reset marker: fresh budget, intact history, self-expiring."""
import csv
from datetime import date

import pytest

from tools.supervisor import day_pnl, read_day_reset


def journal(tmp_path, rows, day="2026-07-30"):
    p = tmp_path / "j.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["timestamp", "profit"])
        w.writeheader()
        for stamp, profit in rows:
            w.writerow({"timestamp": f"{day}T{stamp}+00:00", "profit": profit})
    return str(p)


def marker(tmp_path, text):
    p = tmp_path / ".day_reset"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_since_excludes_everything_before_it(tmp_path):
    j = journal(tmp_path, [("10:00:00", -500.0), ("12:00:00", -400.0),
                           ("14:00:00", 25.0)])
    assert day_pnl(j, date(2026, 7, 30)) == -875.0
    assert day_pnl(j, date(2026, 7, 30), since="2026-07-30T13:00:00+00:00") == 25.0


def test_the_boundary_trade_itself_is_excluded(tmp_path):
    """`since` is 'count from AFTER this', so the marking instant is not
    counted twice."""
    j = journal(tmp_path, [("12:00:00", -400.0), ("12:00:01", -10.0)])
    got = day_pnl(j, date(2026, 7, 30), since="2026-07-30T12:00:00+00:00")
    assert got == -10.0


def test_a_reset_gives_the_rest_of_the_day_a_clean_budget(tmp_path):
    j = journal(tmp_path, [("10:00:00", -900.0)])
    assert day_pnl(j, date(2026, 7, 30), since="2026-07-30T11:00:00+00:00") == 0.0


def test_the_journal_is_never_modified(tmp_path):
    """A marker must leave history intact - every analysis in this repo reads
    the journal and would silently become wrong if rows were deleted."""
    j = journal(tmp_path, [("10:00:00", -900.0)])
    before = open(j, encoding="utf-8").read()
    day_pnl(j, date(2026, 7, 30), since="2026-07-30T11:00:00+00:00")
    assert open(j, encoding="utf-8").read() == before


def test_a_marker_from_a_previous_day_is_ignored(tmp_path):
    """Otherwise one forgotten reset disables the cap forever."""
    p = marker(tmp_path, "2026-07-29T22:00:00+00:00")
    assert read_day_reset(p, day=date(2026, 7, 30)) is None


def test_todays_marker_is_honoured(tmp_path):
    p = marker(tmp_path, "2026-07-30T20:00:00+00:00")
    assert read_day_reset(p, day=date(2026, 7, 30)) == "2026-07-30T20:00:00+00:00"


def test_a_missing_or_unreadable_marker_means_no_reset(tmp_path):
    assert read_day_reset(str(tmp_path / "nope"), day=date(2026, 7, 30)) is None
    p = marker(tmp_path, "")
    assert read_day_reset(p, day=date(2026, 7, 30)) is None
    p = marker(tmp_path, "not-a-timestamp")
    assert read_day_reset(p, day=date(2026, 7, 30)) is None


def test_no_since_is_identical_to_the_old_behaviour(tmp_path):
    j = journal(tmp_path, [("10:00:00", -500.0), ("12:00:00", -400.0)])
    assert day_pnl(j, date(2026, 7, 30)) == day_pnl(j, date(2026, 7, 30), since=None)


def test_reset_still_rounds_to_cents(tmp_path):
    j = journal(tmp_path, [("10:00:00", -1.0)] + [("11:00:0%d" % i, -0.1) for i in range(9)])
    got = day_pnl(j, date(2026, 7, 30), since="2026-07-30T10:30:00+00:00")
    assert got == round(got, 2)
