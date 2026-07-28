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

# How much evidence it takes to move a probability estimate. The blended
# estimate is (hits + PRIOR * p_theory) / (n + PRIOR), so PRIOR is literally
# "how many imaginary trades at the theoretical rate we start from". At 2000,
# a 200-digit window carries 1/11th of the weight, so a 53% fluke moves the
# estimate by ~0.3 points while a genuine 58% bias over 2,000 digits moves it
# by ~4. That asymmetry is the entire point.
DEFAULT_PRIOR = 2000.0


# Rank on the quoted house margin: the one number known exactly, right now.
def score(row: dict[str, Any]) -> float:
    """Lower is better. `edge_pct` is what the quote charges you."""
    return float(row["edge_pct"])


def blended_win_prob(theoretical: float, hits: int, n: int,
                     prior: float = DEFAULT_PRIOR) -> float:
    """Theoretical rate, pulled toward what the ticks actually did.

    Every digit contract's probability is arithmetic, not opinion:
    DIGITOVER:4 wins on 5,6,7,8,9 - five outcomes in ten, exactly 50%, on
    every symbol, always. So observing 53% over 200 digits does not mean the
    probability is 53%; it means a 50% process produced 53% this time, which
    it does roughly a third of the time.

    The blend is the standard Beta-Binomial posterior mean, which is just a
    weighted average of the two:

        p = (hits + prior * theoretical) / (n + prior)

    With prior=2000 the evidence has to be both large and persistent before
    it counts. That is deliberate: the previous version acted on any window
    clearing z>=2 and measured 10.89% WORSE than abstaining, because the
    winner of 60 noisy estimates is usually the luckiest rather than the
    best. Shrinkage does not decide whether a bias exists - it just refuses
    to be moved by a sample too small to tell.
    """
    if n <= 0 or prior < 0:
        return theoretical
    return (hits + prior * theoretical) / (n + prior)


def expected_value(win_prob: float, payout: float, ask: float) -> float:
    """Expected profit per 1.0 staked, at a given win probability.

    `payout` and `ask` come from the live quote and are facts; `win_prob` is
    the estimate. Which is why the estimate is the part that gets shrunk.
    """
    if ask <= 0:
        return 0.0
    return (win_prob * payout - ask) / ask


def blended_score(row: dict[str, Any], prior: float = DEFAULT_PRIOR) -> float:
    """Expected value using the blended probability. HIGHER is better.

    Falls back to the quoted edge when a row carries no tick evidence -
    price-resolved CALL/PUT, or a symbol whose history could not be fetched.
    """
    theoretical = float(row.get("win_prob", 0.0))
    hits, n = row.get("obs_hits"), row.get("obs_n")
    if hits is None or not n:
        return -float(row["edge_pct"]) / 100.0
    p = blended_win_prob(theoretical, int(hits), int(n), prior)
    return expected_value(p, float(row["payout"]), float(row["ask_price"]))


def _ranker(mode: str, prior: float):
    """(key, reverse) for sorting. `global_best` minimises the quoted edge;
    `blended_ev` maximises expected value at the blended probability."""
    if mode == "blended_ev":
        return (lambda r: blended_score(r, prior)), True
    return score, False


def best_per_symbol(rows: Iterable[dict[str, Any]], mode: str = "global_best",
                    prior: float = DEFAULT_PRIOR) -> dict[str, dict[str, Any]]:
    """Stage 1: the best leg quoted for each symbol."""
    key, prefer_high = _ranker(mode, prior)
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        sym = row["symbol"]
        if sym not in best:
            best[sym] = row
        elif (key(row) > key(best[sym])) if prefer_high else (key(row) < key(best[sym])):
            best[sym] = row
    return best


def pick(rows: Iterable[dict[str, Any]],
         veto: set[tuple[str, str | None]] | None = None,
         mode: str = "global_best",
         prior: float = DEFAULT_PRIOR) -> tuple[dict[str, Any] | None,
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

    stage1 = best_per_symbol(candidates, mode, prior)
    if not stage1:
        return None, {}
    key, prefer_high = _ranker(mode, prior)
    winner = (max if prefer_high else min)(stage1.values(), key=key)
    return winner, stage1


def summarise(stage1: dict[str, dict[str, Any]], winner: dict[str, Any] | None,
              mode: str = "global_best", prior: float = DEFAULT_PRIOR) -> str:
    """One line showing every symbol's best and which one won.

    Under `blended_ev` the observed rate and the blended one are both shown,
    so the gap between "what the ticks did" and "what that is worth as
    evidence" is visible rather than buried in a score.
    """
    if not stage1:
        return "selection: nothing quoted"
    parts = []
    for sym in sorted(stage1):
        r = stage1[sym]
        label = r["contract_type"] + ("" if r["barrier"] is None else f":{r['barrier']}")
        mark = " <-" if winner is not None and r is winner else ""
        if mode == "blended_ev" and r.get("obs_n"):
            obs = r["obs_hits"] / r["obs_n"]
            p = blended_win_prob(float(r["win_prob"]), int(r["obs_hits"]),
                                 int(r["obs_n"]), prior)
            parts.append(f"{sym} {label} obs {obs:.1%}->blend {p:.2%} "
                         f"EV {blended_score(r, prior):+.3%}{mark}")
        else:
            parts.append(f"{sym} {label} {r['edge_pct']:.2f}%{mark}")
    head = "per-symbol best" if mode != "blended_ev" else "per-symbol best (blended EV)"
    return head + ": " + " | ".join(parts)
