"""Backtest Rise/Fall: binary at expiry, not a bracket.

Different from the multiplier backtest in the way that matters. A multiplier
resolves when price touches take-profit or stop-loss, so the path decides.
A Rise/Fall contract ignores the path entirely - it compares the close at
expiry with the entry price, once. Price can travel far in your favour and
still lose if it comes back.

Three things kept deliberately unfavourable, because a backtest exists to
argue against the strategy:

1. Entry is the CLOSE of the signal candle, and expiry is measured from
   there. No entering on the high, no hindsight fill.
2. An exact tie loses. The specification says so, and it is also the
   pessimistic reading.
3. The payout is applied as quoted, so a win pays less than the stake risked
   - which is the whole reason a coin-flip strategy loses.

Break-even win rate is 1/payout_multiple. At the 1.9231x these indices quote
that is 52.0%; at the PDF's own 1.78x floor it is 56.2%. Those are the
numbers the specification's claimed ">= 62%" has to clear.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .signals import Strategy


@dataclass
class RFTrade:
    index: int
    direction: int
    entry: float
    exit_price: float
    exit_index: int
    won: bool
    profit: float
    score_reason: str


@dataclass
class RFResult:
    trades: list[RFTrade] = field(default_factory=list)
    evaluated: int = 0
    skipped_neutral: int = 0

    @property
    def count(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.won)

    @property
    def win_rate(self) -> float:
        return self.wins / self.count if self.count else 0.0

    @property
    def net(self) -> float:
        return sum(t.profit for t in self.trades)

    @property
    def trade_rate(self) -> float:
        """Share of evaluated candles that produced a trade."""
        return self.count / self.evaluated if self.evaluated else 0.0


def break_even_win_rate(payout_multiple: float) -> float:
    """Win rate needed to break even. 1.9231x -> 52.0%, 1.78x -> 56.2%."""
    if payout_multiple <= 1:
        raise ValueError("payout_multiple must exceed 1")
    return 1.0 / payout_multiple


def simulate_rise_fall(candles: Sequence[dict[str, Any]], strategy: Strategy, *,
                       stake: float = 10.0, payout_multiple: float = 1.9231,
                       duration_bars: int = 5,
                       warmup: int = 60,
                       one_at_a_time: bool = True) -> RFResult:
    """Replay a Rise/Fall strategy. `duration_bars` is the contract length."""
    res = RFResult()
    n = len(candles)
    if n < warmup + duration_bars + 2:
        return res

    i = warmup
    while i < n - duration_bars:
        res.evaluated += 1
        sig = strategy.evaluate(candles[: i + 1])   # no look-ahead
        if sig is None or sig.direction == 0:
            res.skipped_neutral += 1
            i += 1
            continue

        entry = float(candles[i]["close"])
        exit_i = i + duration_bars
        exit_price = float(candles[exit_i]["close"])

        if sig.direction > 0:
            won = exit_price > entry          # exact tie loses, per the spec
        else:
            won = exit_price < entry
        profit = stake * (payout_multiple - 1.0) if won else -stake

        res.trades.append(RFTrade(
            index=i, direction=sig.direction, entry=entry,
            exit_price=exit_price, exit_index=exit_i, won=won,
            profit=round(profit, 2), score_reason=sig.reason,
        ))
        i = exit_i + 1 if one_at_a_time else i + 1
    return res
