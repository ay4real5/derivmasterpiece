"""Comparing how trades were SELECTED, without fooling ourselves.

`study-report` exists to answer one question: does studying digit history
before a stake beat not studying it? Two things made the first version
unable to answer that.

1. IT COMPARED THE WRONG PAIR. It looked for a `rotation` bucket, but once
   `scan_trade.study.enabled` is true every trade is labelled `study` or
   `study-abstain` and `rotation` never appears at all. So it reported "not
   enough trades" while sitting on hundreds of them.

2. THE COMPARISON IS CONFOUNDED BY CONSTRUCTION. The deep review runs after
   a LOSS, which is exactly when the martingale ladder is elevated. Measured
   on live data: study-selected trades averaged a $34.17 stake against
   $13.24 for abstained ones. Comparing average PnL per trade between those
   groups mostly measures stake size, not selection quality — the study
   group would look better simply for betting more, and worse in a losing
   streak, regardless of whether the study knows anything.

So the primary metric here is WIN RATE, which does not move with stake size,
and the comparison is also reported per stake rung so like is matched with
like. Return per dollar staked is shown too, but win rate is the one to
trust: on these ~50% contracts, a selector with real information wins more
often, and no amount of stake variation can manufacture that.
"""
from __future__ import annotations

import csv
import math
from typing import Any, Iterable


def load_settled(path: str) -> list[dict[str, Any]]:
    """Journal rows that actually settled, with numbers parsed."""
    out: list[dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("profit") or "").strip()
            if not raw:
                continue  # dry-run / unsettled
            try:
                profit = float(raw)
                stake = float(row.get("stake") or 0)
            except ValueError:
                continue
            out.append({
                "selector": (row.get("selector") or "").strip() or "(pre-study)",
                "stake": stake,
                "profit": profit,
                "timestamp": row.get("timestamp") or "",
            })
    return out


def summarise(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    n = len(rows)
    if n == 0:
        return {"trades": 0, "wins": 0, "win_rate": 0.0, "pnl": 0.0,
                "staked": 0.0, "avg": 0.0, "pct_of_staked": 0.0, "mean_stake": 0.0}
    wins = sum(1 for r in rows if r["profit"] > 0)
    pnl = sum(r["profit"] for r in rows)
    staked = sum(r["stake"] for r in rows)
    return {
        "trades": n,
        "wins": wins,
        "win_rate": wins / n,
        "pnl": pnl,
        "staked": staked,
        "avg": pnl / n,
        "pct_of_staked": (pnl / staked * 100) if staked else 0.0,
        "mean_stake": staked / n,
    }


def by_selector(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        buckets.setdefault(r["selector"], []).append(r)
    return {k: summarise(v) for k, v in buckets.items()}


def win_rate_gap(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, float, float]:
    """(gap, standard error, sigma) for a's win rate minus b's.

    Standard two-proportion error. Sigma under ~1.96 means the difference is
    consistent with chance — which, on an independent digit stream, is the
    result to expect.
    """
    n1, n2 = a["trades"], b["trades"]
    if n1 == 0 or n2 == 0:
        return 0.0, 0.0, 0.0
    p1, p2 = a["win_rate"], b["win_rate"]
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    gap = p1 - p2
    return gap, se, (gap / se if se else 0.0)


def stake_matched(rows: Iterable[dict[str, Any]], a: str, b: str,
                  min_per_cell: int = 10) -> list[dict[str, Any]]:
    """Compare selectors `a` and `b` within each stake rung.

    Matching on stake removes the ladder confound: a $5 study trade is
    compared only against $5 abstained trades. Rungs where either side has
    fewer than `min_per_cell` trades are dropped rather than reported as
    though they meant something.
    """
    rows = list(rows)
    rungs = sorted({r["stake"] for r in rows})
    out: list[dict[str, Any]] = []
    for stake in rungs:
        sa = summarise(r for r in rows if r["selector"] == a and r["stake"] == stake)
        sb = summarise(r for r in rows if r["selector"] == b and r["stake"] == stake)
        if sa["trades"] < min_per_cell or sb["trades"] < min_per_cell:
            continue
        gap, se, sigma = win_rate_gap(sa, sb)
        out.append({"stake": stake, "a": sa, "b": sb,
                    "gap": gap, "se": se, "sigma": sigma})
    return out


def pooled_matched_gap(cells: list[dict[str, Any]]) -> tuple[float, float, float]:
    """Combine per-rung win-rate gaps, weighting each rung by its effective
    sample size. Equal weight per rung would let a rung with 12 trades
    outvote one with 300.
    """
    if not cells:
        return 0.0, 0.0, 0.0
    weights, gaps = [], []
    for c in cells:
        n1, n2 = c["a"]["trades"], c["b"]["trades"]
        w = (n1 * n2) / (n1 + n2)  # harmonic-style effective n
        weights.append(w)
        gaps.append(c["gap"])
    total = sum(weights)
    gap = sum(g * w for g, w in zip(gaps, weights)) / total
    # Pooled SE from the per-cell SEs, weighted the same way.
    var = sum((c["se"] * w) ** 2 for c, w in zip(cells, weights)) / (total ** 2)
    se = math.sqrt(var)
    return gap, se, (gap / se if se else 0.0)
