import csv

import pytest

from deriv_bot.reporting import (
    by_selector,
    load_settled,
    pooled_matched_gap,
    stake_matched,
    summarise,
    win_rate_gap,
)

FIELDS = ["timestamp", "symbol", "contract_type", "barrier",
          "stake", "payout", "profit", "balance_after", "reason", "selector"]


def write_journal(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return str(path)


def trade(selector, stake, profit, ts="2026-07-28T01:00:00+00:00"):
    return {"timestamp": ts, "symbol": "R_10", "contract_type": "DIGITEVEN",
            "barrier": "", "stake": stake, "payout": stake * 1.95,
            "profit": profit, "balance_after": 1000, "reason": "x",
            "selector": selector}


def test_load_settled_skips_unsettled_rows(tmp_path):
    p = write_journal(tmp_path / "j.csv", [
        trade("study", 5, 4.77),
        {**trade("study", 5, 0), "profit": ""},   # dry-run
    ])
    assert len(load_settled(p)) == 1


def test_load_settled_labels_pre_selector_rows(tmp_path):
    p = write_journal(tmp_path / "j.csv", [{**trade("", 5, 4.77), "selector": ""}])
    assert load_settled(p)[0]["selector"] == "(pre-study)"


def test_summarise_basics():
    s = summarise([{"stake": 10, "profit": 5}, {"stake": 10, "profit": -10}])
    assert s["trades"] == 2
    assert s["wins"] == 1
    assert s["win_rate"] == 0.5
    assert s["pnl"] == -5
    assert s["mean_stake"] == 10


def test_summarise_empty():
    assert summarise([])["trades"] == 0


def test_by_selector_groups(tmp_path):
    p = write_journal(tmp_path / "j.csv", [
        trade("study", 10, 9), trade("study-abstain", 5, -5),
    ])
    b = by_selector(load_settled(p))
    assert set(b) == {"study", "study-abstain"}
    assert b["study"]["trades"] == 1


def test_win_rate_gap_sign_and_sigma():
    a = {"trades": 100, "win_rate": 0.60}
    b = {"trades": 100, "win_rate": 0.50}
    gap, se, sigma = win_rate_gap(a, b)
    assert gap == pytest.approx(0.10)
    assert sigma > 1.0


def test_win_rate_gap_with_an_empty_side():
    assert win_rate_gap({"trades": 0, "win_rate": 0}, {"trades": 5, "win_rate": 0.5}) == (0.0, 0.0, 0.0)


def test_stake_matched_compares_within_a_rung(tmp_path):
    # the confound this exists to remove: the deep study fires after a loss,
    # so study trades sit on higher ladder rungs than abstained ones
    rows = ([trade("study", 10, 9) for _ in range(10)]
            + [trade("study", 10, -10) for _ in range(10)]
            + [trade("study-abstain", 10, 9) for _ in range(15)]
            + [trade("study-abstain", 10, -10) for _ in range(5)])
    p = write_journal(tmp_path / "j.csv", rows)
    cells = stake_matched(load_settled(p), "study", "study-abstain")
    assert len(cells) == 1
    c = cells[0]
    assert c["stake"] == 10
    assert c["a"]["win_rate"] == pytest.approx(0.5)
    assert c["b"]["win_rate"] == pytest.approx(0.75)
    assert c["gap"] == pytest.approx(-0.25)


def test_stake_matched_drops_thin_cells(tmp_path):
    rows = ([trade("study", 320, 300) for _ in range(3)]
            + [trade("study-abstain", 320, -320) for _ in range(3)])
    p = write_journal(tmp_path / "j.csv", rows)
    assert stake_matched(load_settled(p), "study", "study-abstain") == []


def test_stake_matched_ignores_rungs_only_one_side_used(tmp_path):
    # study-only high rungs must not be compared against nothing
    rows = ([trade("study", 160, 150) for _ in range(20)]
            + [trade("study-abstain", 5, 4) for _ in range(20)])
    p = write_journal(tmp_path / "j.csv", rows)
    assert stake_matched(load_settled(p), "study", "study-abstain") == []


def test_pooled_gap_weights_by_effective_sample_size():
    # a 12-trade rung must not outvote a 300-trade rung
    big = {"stake": 5, "a": {"trades": 300, "win_rate": 0.50},
           "b": {"trades": 300, "win_rate": 0.50}, "gap": 0.0, "se": 0.04, "sigma": 0.0}
    small = {"stake": 40, "a": {"trades": 6, "win_rate": 1.0},
             "b": {"trades": 6, "win_rate": 0.0}, "gap": 1.0, "se": 0.2, "sigma": 5.0}
    gap, _, _ = pooled_matched_gap([big, small])
    assert gap < 0.1  # dominated by the large rung, not the fluke


def test_pooled_gap_with_no_cells():
    assert pooled_matched_gap([]) == (0.0, 0.0, 0.0)
