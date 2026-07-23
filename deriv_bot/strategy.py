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
