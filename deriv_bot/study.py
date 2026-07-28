"""Pre-trade study: score candidate contracts against recent digit history
before staking, instead of taking whatever the round-robin lands on.

Read the warning before trusting anything this produces.

`scan-trade` historically chose by rotation and quoted margin alone, and
looked at no history at all. This module adds the "study before the next
stake" step: for each candidate leg it measures how often that leg would
actually have won over the last N digits, compares that against the
theoretical rate, and only prefers a leg when the gap is large enough to be
unlikely from chance.

WHY THE ABSTENTION RULE IS THE IMPORTANT PART: over 200 digits a fair 50%
leg still lands anywhere from roughly 43% to 57% about a third of the time.
Picking the highest observed rate with no significance test is therefore
mostly picking the luckiest recent noise, which is exactly the behaviour
that feels like analysis while adding nothing. `choose` returns None unless
a leg clears `min_z`, and the caller falls back to the cheapest quoted
margin — the one lever that is real.

Deriv's digits have so far passed every independence test in this repo
(see `AdaptiveBiasStrategy`'s docstring: uniformity chi-square 4.6 vs 16.9,
lag-1 transition 72.7 vs 103). If that holds, this module will abstain
almost always, and that is the correct outcome rather than a bug. Use
`main.py study-report` to check whether study-selected trades actually beat
rotation-selected ones on the same account, and `main.py independence-test`
to re-measure whether there is any signal to study at all.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from .backtester import last_digit
from .edge import theoretical_win_prob
from .strategy import leg_wins

# Legs that resolve on price, not on the last digit, so digit history says
# nothing about them.
PRICE_RESOLVED = ("CALL", "PUT")


def digits_from_ticks(prices: Iterable[Any], pip_size: int) -> list[int]:
    """Last digit of each quote at full pip precision.

    `pip_size` is not optional. Deriv sends quotes as JSON numbers, so 531.70
    arrives as 531.7 and a naive `str(price)[-1]` reads 7 — the digit 0 would
    then never appear in the sample at all. That biases every Over/Under
    score in one direction and would make the study confidently wrong rather
    than merely useless. `backtester.last_digit` already solves this; reuse
    it rather than reimplementing.
    """
    digits: list[int] = []
    for price in prices:
        try:
            digits.append(last_digit(price, pip_size))
        except (TypeError, ValueError):
            continue  # skip an unparseable quote rather than abort the study
    return digits


def score_leg(digits: list[int], kind: str, barrier: str | None) -> dict[str, Any] | None:
    """Observed vs theoretical hit rate for one leg, with a binomial z-score.

    Returns None for price-resolved legs (CALL/PUT), which cannot be judged
    from digits — `AdaptiveBiasStrategy` rejects them for the same reason.
    """
    if kind in PRICE_RESOLVED:
        return None
    if not digits:
        return None
    expected = theoretical_win_prob(kind, barrier)
    n = len(digits)
    hits = sum(1 for d in digits if leg_wins(kind, barrier, d))
    observed = hits / n
    # Standard error under the null "the leg wins at its theoretical rate".
    denom = math.sqrt(expected * (1.0 - expected) / n) if 0.0 < expected < 1.0 else 0.0
    z = (observed - expected) / denom if denom else 0.0
    return {
        "contract_type": kind,
        "barrier": barrier,
        "n": n,
        "hits": hits,
        "observed": observed,
        "expected": expected,
        "z": z,
    }


def score_legs(digits: list[int], legs: Iterable[tuple[str, str | None]]) -> list[dict[str, Any]]:
    """`score_leg` across many legs, skipping the ones that cannot be scored."""
    scored = []
    for kind, barrier in legs:
        row = score_leg(digits, kind, barrier)
        if row is not None:
            scored.append(row)
    return scored


def observed_ev(row: dict[str, Any], quote: dict[str, Any]) -> float:
    """Expected value per unit staked, using the OBSERVED hit rate and the
    REAL quoted payout. The payout is a fact; the hit rate is an estimate —
    that asymmetry is why `choose` gates on significance.
    """
    ask = float(quote["ask_price"])
    if ask <= 0:
        return 0.0
    return (row["observed"] * float(quote["payout"]) - ask) / ask


def choose(scored: list[dict[str, Any]], quotes: dict[tuple[str, str | None], dict[str, Any]],
           min_z: float = 2.0, mode: str = "momentum") -> tuple[dict[str, Any] | None, str]:
    """Pick the best-scoring leg that also has a live quote, or abstain.

    Returns `(winner_or_None, rationale)`. The rationale is always populated
    so the log records why it chose or abstained — an abstention with its
    numbers is more informative than silence.

    `mode="momentum"` prefers legs running ABOVE their theoretical rate;
    `"reversion"` prefers those running below, on the "due" theory. Both are
    offered because both are testable; neither is endorsed.
    """
    if not scored:
        return None, "study: nothing scoreable (all legs price-resolved or no digits)"

    quotable = [r for r in scored if (r["contract_type"], r["barrier"]) in quotes]
    if not quotable:
        return None, "study: no scored leg had a live quote this cycle"

    # momentum wants the largest positive z, reversion the largest negative.
    signed = (lambda r: r["z"]) if mode == "momentum" else (lambda r: -r["z"])
    best = max(quotable, key=signed)
    strength = signed(best)

    label = _label(best)
    detail = (f"{label} {best['observed']:.1%} vs {best['expected']:.1%} expected "
              f"over {best['n']} digits (z={best['z']:+.2f})")

    if strength < min_z:
        return None, (f"study: ABSTAINED — best candidate {detail} does not clear "
                      f"z>={min_z:.1f}; falling back to cheapest quoted margin")

    quote = quotes[(best["contract_type"], best["barrier"])]
    ev = observed_ev(best, quote)
    return best, (f"study: {mode} picked {detail}, observed EV {ev:+.2%} per $1 "
                  f"at the quoted payout")


def _label(row: dict[str, Any]) -> str:
    b = row["barrier"]
    return row["contract_type"] + ("" if b is None else f":{b}")


def summarise(scored: list[dict[str, Any]]) -> str:
    """One-line-per-leg dump of the study table, for the log."""
    if not scored:
        return "study: no scoreable legs"
    parts = [f"{_label(r)} {r['observed']:.1%}/{r['expected']:.1%} z={r['z']:+.2f}"
             for r in sorted(scored, key=lambda r: -r["z"])]
    return "study table (observed/expected): " + " | ".join(parts)
