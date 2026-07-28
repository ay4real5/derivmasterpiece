"""Picking which contract to trade, out of everything quoted this cycle.

Two stages, as asked for: the best leg within each symbol, then the best of
those across all symbols. Worth stating once because it saves code - taking
the max per symbol and then the max of those is the same answer as one
global max over all 60 quotes, so `pick` computes the global argmax and
reports the per-symbol stage for readability.

WHY THIS REPLACES THE ROUND-ROBIN. The old path quoted all 60 combinations
and then let two counters force one symbol and one category, discarding the
other 59. Measured over 1,669 real trades, that made the bot pay a mean
house edge of **2.967%** while the cheapest quote in the same cycle was
routinely 2.25% - 44.4% of trades went out at 3.75-3.80% because the
rotation's turn said so. On the volume traded so far that cost roughly
$471-636 for nothing. Variety was never worth anything: mixing margins
blends what you pay, it cannot change the sign.

WHY THE SCORE IS THE QUOTED EDGE, NOT THE OBSERVED WIN RATE. The edge comes
from the live quote and is a fact. An observed win rate over 200 digits is
an estimate, and picking the maximum of 60 such estimates every cycle
selects whichever leg is luckiest far more often than whichever is best -
the existing study, which does something milder, measured 10.89% WORSE than
abstaining, stake-matched. So tick analysis is allowed to veto a pick or
break a tie, never to drive it.
"""
from __future__ import annotations

from typing import Any, Iterable

# Rank on the quoted house margin: the one number known exactly, right now.
def score(row: dict[str, Any]) -> float:
    """Lower is better. `edge_pct` is what the quote charges you."""
    return float(row["edge_pct"])


def best_per_symbol(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Stage 1: the cheapest leg quoted for each symbol."""
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        sym = row["symbol"]
        if sym not in best or score(row) < score(best[sym]):
            best[sym] = row
    return best


def pick(rows: Iterable[dict[str, Any]],
         veto: set[tuple[str, str | None]] | None = None) -> tuple[dict[str, Any] | None,
                                                                   dict[str, dict[str, Any]]]:
    """Stage 2: the best of the per-symbol winners.

    `veto` removes (contract_type, barrier) legs the caller does not want -
    the hook for tick analysis to exclude something without being allowed to
    choose. If vetoing empties the field, the veto is ignored rather than
    skipping the cycle: an empty field means the veto was too aggressive,
    and not trading is a decision the caller makes explicitly, not a
    side-effect of a filter.
    """
    rows = list(rows)
    if not rows:
        return None, {}

    candidates = rows
    if veto:
        filtered = [r for r in rows if (r["contract_type"], r["barrier"]) not in veto]
        if filtered:
            candidates = filtered

    stage1 = best_per_symbol(candidates)
    if not stage1:
        return None, {}
    winner = min(stage1.values(), key=score)
    return winner, stage1


def summarise(stage1: dict[str, dict[str, Any]], winner: dict[str, Any] | None) -> str:
    """One line showing every symbol's best and which one won."""
    if not stage1:
        return "selection: nothing quoted"
    parts = []
    for sym in sorted(stage1):
        r = stage1[sym]
        label = r["contract_type"] + ("" if r["barrier"] is None else f":{r['barrier']}")
        mark = " <-" if winner is not None and r is winner else ""
        parts.append(f"{sym} {label} {r['edge_pct']:.2f}%{mark}")
    return "per-symbol best: " + " | ".join(parts)
