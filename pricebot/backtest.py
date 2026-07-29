"""Replay a strategy over real candles, honestly.

A backtest exists to talk you OUT of a strategy, so every ambiguity here is
resolved against the strategy rather than for it. Three specific ways a
backtest flatters itself, and what is done about each:

1. **Look-ahead.** The strategy is only ever shown `candles[:i]` and the
   position opens at `candles[i]`'s close - never the high, never a price
   from a later bar. A single off-by-one here manufactures an edge out of
   nothing, so `_evaluate_at` slices explicitly rather than passing the full
   series and trusting the strategy to behave.

2. **The same-candle ambiguity.** When a candle's high reaches the
   take-profit AND its low reaches the stop-loss, one-minute OHLC cannot say
   which came first. Assuming the good one wins is the single most common way
   a backtest lies. `_resolve` always assumes the STOP hit first. Real
   results will be somewhere between that and the optimistic reading; this
   deliberately reports the pessimistic end.

3. **Forgetting the fee.** Commission is charged per position and is the
   entire expected cost of the strategy - on a driftless series the bracket
   itself is a fair bet, so `net` is the only number worth reading and
   `gross` is there to show how much of it the fee ate.

The comparison that matters is against `never`, which trades nothing and
therefore returns exactly zero. A strategy is not required to be profitable
to be interesting, but it IS required to beat doing nothing, and most will
not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .signals import Signal, Strategy
from .symbol_cost import move_for_hold, realised_vol


@dataclass
class Trade:
    index: int
    direction: int
    entry: float
    take_profit: float
    stop_loss: float
    exit_price: float
    exit_index: int
    gross: float
    commission: float
    reason: str
    outcome: str  # "take_profit" | "stop_loss" | "unresolved"

    @property
    def net(self) -> float:
        return self.gross - self.commission


@dataclass
class Result:
    trades: list[Trade] = field(default_factory=list)
    candles_seen: int = 0

    @property
    def count(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.gross > 0)

    @property
    def win_rate(self) -> float:
        return self.wins / self.count if self.count else 0.0

    @property
    def gross(self) -> float:
        return sum(t.gross for t in self.trades)

    @property
    def commission(self) -> float:
        return sum(t.commission for t in self.trades)

    @property
    def net(self) -> float:
        return sum(t.net for t in self.trades)

    @property
    def unresolved(self) -> int:
        return sum(1 for t in self.trades if t.outcome == "unresolved")


def _evaluate_at(strategy: Strategy, candles: Sequence[dict[str, Any]],
                 i: int) -> Signal | None:
    """Strategy sees history up to and including bar `i`, never beyond.

    Sliced here rather than inside the strategy so that a strategy which
    ignores the boundary cannot accidentally peek.
    """
    return strategy.evaluate(candles[: i + 1])


def _resolve(candles: Sequence[dict[str, Any]], start: int, direction: int,
             tp_price: float, sl_price: float,
             max_bars: int) -> tuple[str, float, int]:
    """Walk forward until one of the brackets is touched.

    When a single candle spans BOTH levels the stop is assumed to have hit
    first. One-minute OHLC genuinely cannot distinguish the two, and the
    optimistic reading is how backtests invent edges that evaporate live.
    """
    end = min(len(candles), start + 1 + max_bars)
    for j in range(start + 1, end):
        hi = float(candles[j]["high"])
        lo = float(candles[j]["low"])
        if direction > 0:
            hit_sl, hit_tp = lo <= sl_price, hi >= tp_price
        else:
            hit_sl, hit_tp = hi >= sl_price, lo <= tp_price
        if hit_sl:                      # checked first, deliberately
            return "stop_loss", sl_price, j
        if hit_tp:
            return "take_profit", tp_price, j
    last = min(end, len(candles)) - 1
    return "unresolved", float(candles[last]["close"]), last


def simulate(candles: Sequence[dict[str, Any]], strategy: Strategy, *,
             stake: float = 50.0, multiplier: int = 400,
             commission: float = 0.73, granularity: int = 60,
             target_hold_seconds: float = 600.0,
             stop_fraction: float = 0.6,
             vol_window: int = 500,
             max_hold_bars: int | None = None) -> Result:
    """Replay `candles` through `strategy`, one position at a time.

    One position at a time on purpose: it mirrors the live runner, which
    skips a symbol that already has a position open, and it stops a backtest
    from claiming returns that would need capital the account does not have.
    """
    result = Result(candles_seen=len(candles))
    if len(candles) < vol_window + 2:
        return result

    bars = max_hold_bars or max(1, int(target_hold_seconds / granularity) * 6)
    i = vol_window
    while i < len(candles) - 1:
        signal = _evaluate_at(strategy, candles, i)
        if signal is None or not signal.actionable:
            i += 1
            continue

        vol = realised_vol(candles[max(0, i - vol_window): i + 1], granularity)
        if vol <= 0:
            i += 1
            continue

        # Target distance follows the chosen hold time, exactly as live.
        move = move_for_hold(target_hold_seconds, vol)
        entry = float(candles[i]["close"])
        if entry <= 0 or move <= 0:
            i += 1
            continue

        d = entry * move
        if signal.direction > 0:
            tp_price, sl_price = entry + d, entry - d * stop_fraction
        else:
            tp_price, sl_price = entry - d, entry + d * stop_fraction

        outcome, exit_price, exit_i = _resolve(
            candles, i, signal.direction, tp_price, sl_price, bars)

        moved = (exit_price - entry) / entry * signal.direction
        gross = round(stake * multiplier * moved, 2)
        result.trades.append(Trade(
            index=i, direction=signal.direction, entry=entry,
            take_profit=tp_price, stop_loss=sl_price, exit_price=exit_price,
            exit_index=exit_i, gross=gross, commission=commission,
            reason=signal.reason, outcome=outcome,
        ))
        i = exit_i + 1          # one position at a time
    return result


def compare(candles: Sequence[dict[str, Any]], strategies: dict[str, Strategy],
            **kwargs: Any) -> dict[str, Result]:
    """Run several strategies over identical candles.

    Include `never` in `strategies`: it returns exactly zero, and a strategy
    that cannot beat zero has no business being run live.
    """
    return {name: simulate(candles, strat, **kwargs)
            for name, strat in strategies.items()}
