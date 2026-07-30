"""The daily cap must fire AT the limit, not a fraction of a cent past it.

Live on 2026-07-30: the day's realised PnL summed to -899.9999999999989
against a 900.00 cap. `pnl <= -900` was False by 1.1e-12, so the supervisor
reported "within limits, launching" while every child immediately found no
budget, exited after 4s, and was relaunched. A restart loop with the cap
silently not applying - the exact protection the user relies on.
"""
import csv
from datetime import date

import pytest

from tools.supervisor import budget_verdict, day_pnl


def journal(tmp_path, profits, day="2026-07-30"):
    p = tmp_path / "j.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["timestamp", "profit"])
        w.writeheader()
        for i, v in enumerate(profits):
            w.writerow({"timestamp": f"{day}T00:{i // 60:02d}:{i % 60:02d}+00:00",
                        "profit": v})
    return str(p)


def test_the_exact_live_case_now_stops(tmp_path):
    """Many small floats that should total exactly -900."""
    profits = [-3.00] * 300
    p = day_pnl(journal(tmp_path, profits), date(2026, 7, 30))
    assert p == -900.0
    assert budget_verdict(p, 900.0, None) is not None, "cap must fire at the limit"


def test_a_float_sum_that_lands_just_short_is_rounded_to_the_limit(tmp_path):
    """0.1-style values are the ones that do not sum cleanly in binary."""
    profits = [-0.1] * 9000          # exactly -900 in decimal, not in float
    p = day_pnl(journal(tmp_path, profits), date(2026, 7, 30))
    assert p == -900.0
    assert budget_verdict(p, 900.0, None) is not None


def test_day_pnl_is_always_a_whole_number_of_cents(tmp_path):
    profits = [-0.1, -0.2, -0.3, 0.7, -1.11]
    p = day_pnl(journal(tmp_path, profits), date(2026, 7, 30))
    assert p == round(p, 2)
    assert abs(p * 100 - round(p * 100)) < 1e-9


def test_one_cent_inside_the_cap_still_trades(tmp_path):
    """The fix must not make the cap fire early."""
    profits = [-899.99]
    p = day_pnl(journal(tmp_path, profits), date(2026, 7, 30))
    assert budget_verdict(p, 900.0, None) is None


def test_one_cent_past_the_cap_stops(tmp_path):
    profits = [-900.01]
    p = day_pnl(journal(tmp_path, profits), date(2026, 7, 30))
    assert budget_verdict(p, 900.0, None) is not None


@pytest.mark.parametrize("cap", [100.0, 300.0, 700.0, 900.0, 1000.0])
def test_the_boundary_is_inclusive_at_every_cap(cap):
    """'not worse than' is what a limit means."""
    assert budget_verdict(-cap, cap, None) is not None
    assert budget_verdict(-cap + 0.01, cap, None) is None


def test_a_negative_cap_in_config_still_caps(tmp_path):
    """-900 reads perfectly naturally as 'cap at minus nine hundred'."""
    assert budget_verdict(-900.0, -900.0, None) is not None


def test_profit_target_boundary_is_inclusive_too():
    assert budget_verdict(1000.0, 900.0, 1000.0) is not None
    assert budget_verdict(999.99, 900.0, 1000.0) is None
