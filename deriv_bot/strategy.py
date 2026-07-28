"""Digit-trading strategies.

IMPORTANT HONESTY NOTE: Deriv's synthetic-index last digit is generated to be
close to a uniform, independent random draw each tick. A frequency/"reversion"
heuristic like the one below has no proven statistical edge over the long run
— Digits contracts have a built-in payout margin (house edge), so expected
value is negative on average regardless of strategy, absent a genuine edge
this simple heuristic does not claim to have. Treat this as a working example
to backtest and iterate on, not a money-making formula.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from .edge import theoretical_win_prob


@dataclass
class Signal:
    contract_type: str  # "DIGITOVER" | "DIGITUNDER" | "DIGITEVEN" | "DIGITODD"
    barrier: str | None
    reason: str


class Strategy:
    def on_tick(self, digit: int) -> Signal | None:
        raise NotImplementedError


class DigitFrequencyStrategy(Strategy):
    """Watches the last `window` digits. If the fraction of digits at or
    below `over_under_barrier` drifts more than `threshold` away from its
    theoretical baseline, signals the Over/Under contract that bets on it
    reverting toward the baseline.
    """

    def __init__(self, window: int = 100, threshold: float = 0.03, over_under_barrier: int = 4):
        if not 0 <= over_under_barrier <= 9:
            raise ValueError("over_under_barrier must be between 0 and 9")
        self.window = window
        self.threshold = threshold
        self.barrier = over_under_barrier
        self._expected_under_freq = (over_under_barrier + 1) / 10
        self.history: deque[int] = deque(maxlen=window)

    def on_tick(self, digit: int) -> Signal | None:
        self.history.append(digit)
        if len(self.history) < self.window:
            return None

        under_freq = sum(1 for d in self.history if d <= self.barrier) / len(self.history)
        deviation = under_freq - self._expected_under_freq

        if deviation > self.threshold:
            return Signal(
                "DIGITOVER",
                str(self.barrier),
                f"digits<={self.barrier} over-represented ({under_freq:.1%} vs "
                f"{self._expected_under_freq:.1%} expected)",
            )
        if deviation < -self.threshold:
            return Signal(
                "DIGITUNDER",
                str(self.barrier),
                f"digits<={self.barrier} under-represented ({under_freq:.1%} vs "
                f"{self._expected_under_freq:.1%} expected)",
            )
        return None


class EvenOddFrequencyStrategy(Strategy):
    """Same idea as DigitFrequencyStrategy but on the Even/Odd contract pair:
    watches the fraction of even digits in the last `window` ticks and signals
    a reversion bet once it drifts more than `threshold` from the 50% baseline.
    Same honesty caveat as above applies.
    """

    def __init__(self, window: int = 100, threshold: float = 0.03):
        self.window = window
        self.threshold = threshold
        self.history: deque[int] = deque(maxlen=window)

    def on_tick(self, digit: int) -> Signal | None:
        self.history.append(digit)
        if len(self.history) < self.window:
            return None

        even_freq = sum(1 for d in self.history if d % 2 == 0) / len(self.history)
        deviation = even_freq - 0.5

        if deviation > self.threshold:
            return Signal(
                "DIGITODD", None,
                f"even digits over-represented ({even_freq:.1%} vs 50.0% expected)",
            )
        if deviation < -self.threshold:
            return Signal(
                "DIGITEVEN", None,
                f"even digits under-represented ({even_freq:.1%} vs 50.0% expected)",
            )
        return None


class StreakReversalStrategy(Strategy):
    """Bets against a run of `streak_len` consecutive digits landing on the
    same side of `over_under_barrier`.

    IMPORTANT: this is the gambler's fallacy written as code — a streak on
    independent draws carries no information about the next draw. Included
    as a baseline to backtest and compare against, precisely because it's
    the kind of pattern intuition wrongly trusts; see the module docstring.
    """

    def __init__(self, streak_len: int = 4, over_under_barrier: int = 4):
        if not 0 <= over_under_barrier <= 9:
            raise ValueError("over_under_barrier must be between 0 and 9")
        self.streak_len = streak_len
        self.barrier = over_under_barrier
        self._current_side: str | None = None
        self._streak = 0

    def on_tick(self, digit: int) -> Signal | None:
        side = "under" if digit <= self.barrier else "over"
        if side == self._current_side:
            self._streak += 1
        else:
            self._current_side = side
            self._streak = 1

        if self._streak >= self.streak_len:
            self._streak = 0
            if side == "under":
                return Signal(
                    "DIGITOVER", str(self.barrier),
                    f"{self.streak_len} consecutive 'under' digits — betting reversal",
                )
            return Signal(
                "DIGITUNDER", str(self.barrier),
                f"{self.streak_len} consecutive 'over' digits — betting reversal",
            )
        return None


class LowEdgeStrategy(Strategy):
    """Doesn't try to predict anything — that's the point.

    Bets a fixed high-probability contract (default DIGITOVER 0: wins on
    digits 1-9, i.e. 90% of the time) on a fixed cadence. Deriv prices
    high-probability digit contracts with the smallest house margin (~1.9%
    observed, vs ~4% for the 50/50 contracts and up to ~16.7% for longshots),
    so this is the slowest possible expected bleed per dollar staked. It
    still loses on average: it minimizes the house edge, it does not beat it.
    Included as the honest benchmark every "predictive" strategy above has
    to justify itself against.

    Run `main.py scan-edge` and point contract_type/barrier at whatever is
    currently cheapest (e.g. DIGITDIFF if it undercuts DIGITOVER 0).
    """

    CONTRACT_TYPES = ("DIGITOVER", "DIGITUNDER", "DIGITEVEN", "DIGITODD",
                      "DIGITMATCH", "DIGITDIFF", "CALL", "PUT")
    _NO_BARRIER = ("DIGITEVEN", "DIGITODD", "CALL", "PUT")

    def __init__(self, every: int = 15, contract_type: str = "DIGITOVER", barrier: str | int | None = "0"):
        if every < 1:
            raise ValueError("every must be >= 1")
        if contract_type not in self.CONTRACT_TYPES:
            raise ValueError(f"contract_type must be one of {self.CONTRACT_TYPES}")
        if contract_type in self._NO_BARRIER:
            barrier = None
        elif barrier is None or not 0 <= int(barrier) <= 9:
            raise ValueError("barrier must be a digit 0-9 for this contract_type")
        self.every = every
        self.contract_type = contract_type
        self.barrier = None if barrier is None else str(barrier)
        self._ticks_seen = 0

    def on_tick(self, digit: int) -> Signal | None:
        self._ticks_seen += 1
        if self._ticks_seen % self.every != 0:
            return None
        return Signal(
            self.contract_type, self.barrier,
            "fixed cheapest-bet cadence (see scan-edge for current margins)",
        )


class RotationStrategy(Strategy):
    """Cycles through a list of contracts on a fixed tick cadence.

    Predicts nothing — it exists to spread a session across contract types.
    Worth knowing what rotation does and doesn't do: because every contract
    resolves on the same underlying random tick, mixing cannot improve
    expected value. It blends the *margins* you pay (e.g. alternating a
    2.17% contract with 3.85% ones lands you in between) and blends the
    outcome shapes. It is a comfort/variety knob, not an edge.

    contracts: list of "TYPE" or "TYPE:BARRIER" strings, e.g.
        ["DIGITOVER:0", "DIGITEVEN", "CALL"]
    """

    def __init__(self, every: int = 3, contracts: list[str] | None = None):
        if every < 1:
            raise ValueError("every must be >= 1")
        specs = contracts or ["DIGITOVER:0", "DIGITEVEN", "CALL"]
        self.legs: list[tuple[str, str | None]] = []
        for spec in specs:
            kind, _, barrier = str(spec).partition(":")
            kind = kind.strip().upper()
            if kind not in LowEdgeStrategy.CONTRACT_TYPES:
                raise ValueError(f"unknown contract_type {kind!r}")
            if kind in LowEdgeStrategy._NO_BARRIER:
                self.legs.append((kind, None))
            else:
                if barrier == "":
                    raise ValueError(f"{kind} needs a barrier, e.g. {kind}:0")
                if not 0 <= int(barrier) <= 9:
                    raise ValueError("barrier must be a digit 0-9")
                self.legs.append((kind, str(int(barrier))))
        self.every = every
        self._ticks_seen = 0
        self._next_leg = 0

    def on_tick(self, digit: int) -> Signal | None:
        self._ticks_seen += 1
        if self._ticks_seen % self.every != 0:
            return None
        kind, barrier = self.legs[self._next_leg % len(self.legs)]
        self._next_leg += 1
        return Signal(kind, barrier, f"rotation leg {kind}{'' if barrier is None else ':' + barrier}")


def leg_wins(kind: str, barrier: str | None, digit: int) -> bool:
    """Would this contract have won on `digit`? (CALL/PUT need prices, so
    they are excluded from digit-based bias scoring.)"""
    b = int(barrier) if barrier is not None else 0
    if kind == "DIGITOVER":  return digit > b
    if kind == "DIGITUNDER": return digit < b
    if kind == "DIGITEVEN":  return digit % 2 == 0
    if kind == "DIGITODD":   return digit % 2 == 1
    if kind == "DIGITMATCH": return digit == b
    if kind == "DIGITDIFF":  return digit != b
    raise ValueError(f"{kind} cannot be scored from digits alone")


# Kept so existing imports/tests referring to the private name keep working.
_leg_wins = leg_wins


class AdaptiveBiasStrategy(Strategy):
    """Picks whichever candidate contract has been performing best (or worst)
    over the last `window` digits, instead of rotating in a fixed order.

    `mode="momentum"` bets the contract that has been winning most lately;
    `mode="reversion"` bets the one winning least, on the theory it is "due".

    HONEST WARNING, and the reason this class exists mainly to be tested:
    Deriv's digits pass every independence test we have run on live data
    (uniformity chi-square 4.6 vs 16.9 threshold; lag-1 transition chi-square
    72.7 vs 103; 53.3% "over" after 3+ "under" runs, versus 50% expected).
    If the stream carries no information, then neither momentum nor reversion
    can beat a fixed rotation, and the only thing selection changes is WHICH
    house margin you pay. Backtest it against `rotation` before believing it.
    """

    def __init__(self, every: int = 6, window: int = 50, mode: str = "momentum",
                 contracts: list[str] | None = None):
        if every < 1:
            raise ValueError("every must be >= 1")
        if mode not in ("momentum", "reversion"):
            raise ValueError("mode must be 'momentum' or 'reversion'")
        specs = contracts or ["DIGITOVER:0", "DIGITUNDER:9", "DIGITEVEN", "DIGITODD"]
        self.legs: list[tuple[str, str | None]] = []
        for spec in specs:
            kind, _, barrier = str(spec).partition(":")
            kind = kind.strip().upper()
            if kind in ("CALL", "PUT"):
                raise ValueError("CALL/PUT resolve on price, not digits — "
                                 "they cannot be bias-scored; use `rotation` for those")
            if kind not in LowEdgeStrategy.CONTRACT_TYPES:
                raise ValueError(f"unknown contract_type {kind!r}")
            if kind in LowEdgeStrategy._NO_BARRIER:
                self.legs.append((kind, None))
            else:
                if barrier == "" or not 0 <= int(barrier) <= 9:
                    raise ValueError(f"{kind} needs a barrier 0-9, e.g. {kind}:0")
                self.legs.append((kind, str(int(barrier))))
        self.every = every
        self.window = window
        self.mode = mode
        self.history: deque[int] = deque(maxlen=window)
        self._ticks_seen = 0

    def on_tick(self, digit: int) -> Signal | None:
        self.history.append(digit)
        self._ticks_seen += 1
        if self._ticks_seen % self.every != 0 or len(self.history) < self.window:
            return None

        scored = []
        for kind, barrier in self.legs:
            hits = sum(1 for d in self.history if _leg_wins(kind, barrier, d))
            scored.append((hits / len(self.history), kind, barrier))
        rate, kind, barrier = (max(scored) if self.mode == "momentum" else min(scored))
        return Signal(
            kind, barrier,
            f"{self.mode}: {kind}{'' if barrier is None else ':' + barrier} "
            f"hit {rate:.0%} of last {len(self.history)}",
        )


class QuotaRotationStrategy(Strategy):
    """Fixes the bug found in `AdaptiveBiasStrategy`: raw win-rate scoring
    always favours whichever contract has the highest theoretical
    probability, so a 90%-tier contract permanently outscores a 50%-tier
    one and families like Even/Odd or Rise/Fall never get picked at all —
    confirmed on live sessions (212 trades, 100% DIGITOVER/DIGITUNDER, 0
    Even/Odd, 0 Rise/Fall).

    This strategy guarantees every configured family trades its share of
    bets via a weighted round-robin scheduler (deterministic, not random —
    50/25/25 shares land close to exactly 50/25/25 over any decent sample).
    Scoring is only used to pick a leg *within* the family whose turn it
    is, and only for contracts scoreable from digit history; families with
    no digit-scoreable legs (Rise/Fall) alternate their legs evenly instead.

    `families`: list of (name, contract_specs, share). Example:
        [("over_under", ["DIGITOVER:0", "DIGITUNDER:9"], 0.5),
         ("even_odd",   ["DIGITEVEN", "DIGITODD"],       0.25),
         ("rise_fall",  ["CALL", "PUT"],                 0.25)]

    Same honesty note as `AdaptiveBiasStrategy` applies: digits carry no
    detectable bias (see its docstring), so within-family scoring mainly
    determines which side of a coin-flip-priced pair you take, not whether
    you win more. The quota's real job is making sure every requested
    contract family actually appears in the journal.
    """

    DEFAULT_FAMILIES: list[tuple[str, list[str], float]] = [
        ("over_under", ["DIGITOVER:0", "DIGITUNDER:9"], 0.5),
        ("even_odd", ["DIGITEVEN", "DIGITODD"], 0.25),
        ("rise_fall", ["CALL", "PUT"], 0.25),
    ]

    def __init__(self, every: int = 6, window: int = 50, mode: str = "momentum",
                 families: list[tuple[str, list[str], float]] | None = None):
        if every < 1:
            raise ValueError("every must be >= 1")
        if mode not in ("momentum", "reversion"):
            raise ValueError("mode must be 'momentum' or 'reversion'")
        if families is None:
            families = self.DEFAULT_FAMILIES
        if not families or sum(share for _, _, share in families) <= 0:
            raise ValueError("families must be non-empty with shares summing > 0")

        self.every = every
        self.window = window
        self.mode = mode
        self.history: deque[int] = deque(maxlen=window)
        self._ticks_seen = 0
        self._rf_toggle = 0

        total_share = sum(share for _, _, share in families)
        self._family_names: list[str] = []
        self._family_weights: list[float] = []
        self._family_legs: list[list[tuple[str, str | None]]] = []
        self._credit: list[float] = []
        for name, specs, share in families:
            if share <= 0:
                raise ValueError(f"family {name!r} needs a positive share")
            legs: list[tuple[str, str | None]] = []
            for spec in specs:
                kind, _, barrier = str(spec).partition(":")
                kind = kind.strip().upper()
                if kind not in LowEdgeStrategy.CONTRACT_TYPES:
                    raise ValueError(f"unknown contract_type {kind!r}")
                if kind in LowEdgeStrategy._NO_BARRIER:
                    legs.append((kind, None))
                else:
                    if barrier == "" or not 0 <= int(barrier) <= 9:
                        raise ValueError(f"{kind} needs a barrier 0-9, e.g. {kind}:0")
                    legs.append((kind, str(int(barrier))))
            self._family_names.append(name)
            self._family_weights.append(share / total_share)
            self._family_legs.append(legs)
            self._credit.append(0.0)

    def _pick_leg(self, family_idx: int) -> tuple[tuple[str, str | None], float | None]:
        legs = self._family_legs[family_idx]
        scoreable = [(k, b) for k, b in legs if k not in ("CALL", "PUT")]
        if not scoreable:
            leg = legs[self._rf_toggle % len(legs)]
            self._rf_toggle += 1
            return leg, None
        if len(scoreable) == 1:
            return scoreable[0], None

        scored = []
        for kind, barrier in scoreable:
            hits = sum(1 for d in self.history if _leg_wins(kind, barrier, d))
            rate = hits / len(self.history) if self.history else 0.0
            excess = rate - theoretical_win_prob(kind, barrier)
            scored.append((excess, kind, barrier))
        excess, kind, barrier = (max(scored) if self.mode == "momentum" else min(scored))
        return (kind, barrier), excess

    def on_tick(self, digit: int) -> Signal | None:
        self.history.append(digit)
        self._ticks_seen += 1
        if self._ticks_seen % self.every != 0 or len(self.history) < self.window:
            return None

        # Weighted round-robin: every family accrues credit each turn;
        # whoever has the most credit trades and pays it down by 1. This
        # keeps shares proportional AND well-interleaved (not clumped).
        for i in range(len(self._credit)):
            self._credit[i] += self._family_weights[i]
        idx = max(range(len(self._credit)), key=lambda i: self._credit[i])
        self._credit[idx] -= 1.0

        (kind, barrier), excess = self._pick_leg(idx)
        fam = self._family_names[idx]
        reason = f"quota:{fam} -> {kind}{'' if barrier is None else ':' + barrier}"
        if excess is not None:
            reason += f" ({self.mode} excess {excess:+.1%})"
        return Signal(kind, barrier, reason)


STRATEGIES: dict[str, type[Strategy]] = {
    "digit_frequency": DigitFrequencyStrategy,
    "even_odd_frequency": EvenOddFrequencyStrategy,
    "streak_reversal": StreakReversalStrategy,
    "low_edge": LowEdgeStrategy,
    "rotation": RotationStrategy,
    "adaptive_bias": AdaptiveBiasStrategy,
    "quota_rotation": QuotaRotationStrategy,
}


def build_strategy(name: str, **kwargs: Any) -> Strategy:
    try:
        cls = STRATEGIES[name]
    except KeyError:
        raise ValueError(f"unknown strategy '{name}' — choices: {sorted(STRATEGIES)}") from None
    return cls(**kwargs)
