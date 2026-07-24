"""Reads the trade journal and reports where the money actually went.

This is the "understand why" tool: it turns the CSV audit trail into
per-contract performance numbers you can compare against theory. On digit
contracts the honest expectation is: win rate ≈ the contract's theoretical
probability, and average PnL per trade ≈ -(house margin) × stake. If a
strategy's numbers drift far from that over a large sample, that's worth
investigating; over a small sample it's just noise.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from typing import Any


def analyze_journal(path: str) -> dict[str, Any]:
    """Returns overall stats plus per-(contract_type, barrier) buckets for
    every journal row that has a settled profit. Dry-run rows (blank profit)
    are skipped.
    """
    overall = {"trades": 0, "wins": 0, "total_pnl": 0.0, "total_staked": 0.0}
    buckets: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0, "total_staked": 0.0}
    )

    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("profit"):
                continue
            profit = float(row["profit"])
            stake = float(row["stake"]) if row.get("stake") else 0.0
            key = (row.get("contract_type", "?"), row.get("barrier") or "-")
            for stats in (overall, buckets[key]):
                stats["trades"] += 1
                stats["wins"] += 1 if profit > 0 else 0
                stats["total_pnl"] += profit
                stats["total_staked"] += stake

    def _finish(stats: dict[str, Any]) -> dict[str, Any]:
        trades = stats["trades"]
        return {
            **stats,
            "win_rate": (stats["wins"] / trades) if trades else 0.0,
            "avg_pnl": (stats["total_pnl"] / trades) if trades else 0.0,
            "loss_pct_of_staked": (
                (-stats["total_pnl"] / stats["total_staked"] * 100)
                if stats["total_staked"] else 0.0
            ),
        }

    return {
        "overall": _finish(overall),
        "by_contract": {key: _finish(stats) for key, stats in sorted(buckets.items())},
    }
