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


STRATEGIES: dict[str, type[Strategy]] = {
    "digit_frequency": DigitFrequencyStrategy,
    "even_odd_frequency": EvenOddFrequencyStrategy,
    "streak_reversal": StreakReversalStrategy,
    "low_edge": LowEdgeStrategy,
    "rotation": RotationStrategy,
}


def build_strategy(name: str, **kwargs: Any) -> Strategy:
    try:
        cls = STRATEGIES[name]
    except KeyError:
        raise ValueError(f"unknown strategy '{name}' — choices: {sorted(STRATEGIES)}") from None
    return cls(**kwargs)
