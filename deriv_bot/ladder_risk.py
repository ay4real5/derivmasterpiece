"""Match a doubling ladder to the capital behind it.

The useful question about a capped ladder is not "does it lose?" (every
negative-edge bet does) but "how big is one full ladder relative to my
capital, and how often does one arrive?". Those two numbers together decide
whether a run of bad luck is an inconvenience or the end of the account.

Both are computable exactly, so nobody has to argue about them:

- A full ladder of `rungs` costs `base * (2**rungs - 1)`. Seven rungs from a
  $5 base is $635, always.
- At a win probability p, a cycle reaches a full wipeout with probability
  `(1-p)**rungs`, and a cycle lasts `~1/p` trades. So wipeouts arrive every
  `1 / (p * (1-p)**rungs)` trades on average - about one per 258 trades for
  seven rungs at p=0.5, which at 45s a trade is roughly every 3.2 hours.

The counter-intuitive part, and the reason this module exists: 0.78% per
cycle feels negligible, but it is multiplied by how many cycles a day of
unattended running produces. Rarity per cycle is not rarity per day.
"""
from __future__ import annotations


def ladder_cost(base: float, rungs: int,
                sequence: list[float] | None = None) -> float:
    """Cost of a ladder that loses every rung.

    `base * (2**rungs - 1)` only holds for a DOUBLING ladder. A recovery
    ladder (3, 3.25, 6.77, 14.10, ...) climbs at ~2.083x, so the doubling
    formula would understate its worst case. Pass the actual `sequence` and
    it is summed instead of assumed - the worst-case number is the one
    everything else here is derived from, so it should never be guessed.
    """
    if sequence:
        return float(sum(sequence[:rungs] if rungs else sequence))
    return base * (2 ** rungs - 1)


def max_base_for_risk(capital: float, rungs: int, risk_fraction: float,
                      sequence: list[float] | None = None) -> float:
    """Largest base stake keeping one full ladder within `risk_fraction`.

    With an explicit `sequence`, the ladder is scaled as a whole: the answer
    is the base that shrinks the WHOLE ladder to the target, not just the
    first rung.
    """
    if capital <= 0 or rungs < 1:
        return 0.0
    target = capital * risk_fraction
    if sequence:
        total = sum(sequence[:rungs] if rungs else sequence)
        if total <= 0:
            return 0.0
        return sequence[0] * target / total
    return target / (2 ** rungs - 1)


def wipeout_every_n_trades(rungs: int, win_prob: float = 0.5) -> float:
    """Mean trades between full-ladder wipeouts.

    A cycle ends on the first win or after `rungs` losses. It wipes out with
    probability (1-p)**rungs, and averages ~1/p trades, so wipeouts occur
    every 1 / (p * (1-p)**rungs) trades.
    """
    if not 0 < win_prob < 1 or rungs < 1:
        return float("inf")
    return 1.0 / (win_prob * (1 - win_prob) ** rungs)


def summarise(capital: float, base: float, rungs: int,
              win_prob: float = 0.5, seconds_per_trade: float = 45.0,
              sequence: list[float] | None = None) -> dict:
    cost = ladder_cost(base, rungs, sequence)
    every = wipeout_every_n_trades(rungs, win_prob)
    hours = every * seconds_per_trade / 3600.0
    per_day = 24.0 / hours if hours > 0 else 0.0
    return {
        "ladder_cost": cost,
        "pct_of_capital": (cost / capital * 100) if capital > 0 else float("inf"),
        "wipeout_every_n_trades": every,
        "hours_between_wipeouts": hours,
        "wipeouts_per_day": per_day,
        "expected_daily_wipeout_cost": per_day * cost,
        "ladders_capital_covers": (capital / cost) if cost > 0 else float("inf"),
    }
