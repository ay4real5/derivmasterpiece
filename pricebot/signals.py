"""What a strategy must produce, and why it carries more than a direction.

The digit bot bet on the last decimal of a price. This one bets on the price
itself, using Multipliers, Rise/Fall and Touch/No Touch. Those three look
unrelated but they are three questions about the same forecast:

    Rise/Fall   - which way, by when?
    Multiplier  - which way, and how far before I take profit?
    Touch       - how far, and will it get there?

So one `Signal` drives all three, provided it carries direction, expected
move AND horizon. A direction alone cannot size a take-profit or place a
barrier; that is why `expected_move_pct` is not optional.

WHY `never` IS THE DEFAULT STRATEGY. Every trade costs the spread, and the
spread is certain while the forecast is not. A strategy therefore has to beat
"do nothing", not "break even", and `never` is that baseline made runnable so
the comparison is arithmetic rather than argument. The digit bot spent three
days measuring cost per bet and never once compared against not betting.

None of the strategies here is claimed to work. On synthetic indices they
provably cannot - the price is RNG, confirmed by this repo's own independence
tests and 1,561 live ladder cycles. On forex, gold or indices a forecast can
in principle beat chance. Which is why the interface is pluggable and the
backtest exists: to find out, rather than to assume.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass
class Signal:
    """A forecast, expressed so any of the three instruments can execute it.

    `expected_move_pct` is a fraction of price (0.002 = 0.2%), not a
    percentage number - the commonest way to get this wrong by 100x.
    """

    direction: int            # +1 up, -1 down, 0 no view
    expected_move_pct: float  # how far, as a fraction of price
    horizon_seconds: int      # by when
    confidence: float         # 0-1; scales stake and can veto the trade
    reason: str               # logged verbatim so every trade explains itself

    def __post_init__(self) -> None:
        if self.direction not in (-1, 0, 1):
            raise ValueError("direction must be -1, 0 or +1")
        if self.expected_move_pct < 0:
            raise ValueError("expected_move_pct must be >= 0")
        if self.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    @property
    def actionable(self) -> bool:
        """A view with no direction and no expected move is not a trade.

        Returning a Signal of zeros rather than None is a common way for a
        strategy to look busy while doing nothing, so the distinction is
        explicit here rather than left to each caller.
        """
        return self.direction != 0 and self.expected_move_pct > 0


class Strategy:
    """`evaluate` returns a Signal, or None for "no opinion".

    Candles are the newest-last sequence of dicts with open/high/low/close,
    matching what `ticks_history` returns with `style="candles"`.

    `symbol` is OPTIONAL and ignored by every strategy except ones that
    genuinely need to behave differently per symbol (see `FixedNoTouch`,
    which sizes its barrier off it) - added as a keyword-only-in-spirit
    parameter with a default so every existing call site and every existing
    Strategy subclass keeps working unchanged.
    """

    name = "base"

    def evaluate(self, candles: Sequence[dict[str, Any]],
                symbol: str | None = None) -> Signal | None:
        raise NotImplementedError


class NeverTrade(Strategy):
    """Trades nothing, ever. The baseline every other strategy must beat.

    Not a placeholder: on a negative-cost venue this is the highest-scoring
    strategy unless a forecast genuinely carries information, and it is the
    correct default for a bot pointed at RNG synthetics.
    """

    name = "never"

    def evaluate(self, candles: Sequence[dict[str, Any]],
                symbol: str | None = None) -> Signal | None:
        return None


class FixedNoTouch(Strategy):
    """No forecast at all - buys the same NOTOUCH barrier every cycle.

    This is the Touch/No Touch equivalent of `deriv_bot.strategy.LowEdgeStrategy`:
    it does not try to predict anything, it buys a fixed win-rate SHAPE at
    whatever margin `deriv_bot/touch_edge.py` measured for that barrier and
    duration. EV is the same at every barrier (confirmed by that scan's own
    model-free margin calculation) - `barrier_pct` only changes how often you
    win and how large the rare loss is, never the expected cost.

    `direction=0` on the emitted Signal is what tells `build_proposal` this
    is a no-view NOTOUCH rather than a "price will stay put" forecast (see
    `pricebot/instruments.py`'s `no_view` branch).
    """

    name = "fixed_notouch"

    def __init__(self, barrier_pct: float = 0.30, horizon_seconds: int = 300,
                barrier_by_symbol: dict[str, float] | None = None):
        """`barrier_by_symbol` overrides `barrier_pct` for specific symbols.

        Needed because the same percentage barrier does NOT buy the same
        win-rate shape on every symbol - confirmed by scan-touch: R_50 and
        R_75 need different barrier widths (0.30% vs 0.40%) to both land
        near a ~93-96% NOTOUCH win rate at 5 minutes, since they carry
        different volatility. A symbol not listed here falls back to the
        plain `barrier_pct`.
        """
        if barrier_pct <= 0:
            raise ValueError("barrier_pct must be positive")
        if horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        overrides = dict(barrier_by_symbol or {})
        for sym, pct in overrides.items():
            if pct <= 0:
                raise ValueError(f"barrier_by_symbol[{sym!r}] must be positive")
        self.barrier_pct = barrier_pct
        self.horizon_seconds = horizon_seconds
        self.barrier_by_symbol = overrides

    def barrier_for(self, symbol: str | None) -> float:
        if symbol is None:
            return self.barrier_pct
        return self.barrier_by_symbol.get(symbol, self.barrier_pct)

    def evaluate(self, candles: Sequence[dict[str, Any]],
                symbol: str | None = None) -> Signal | None:
        barrier = self.barrier_for(symbol)
        return Signal(
            direction=0,
            expected_move_pct=barrier,
            horizon_seconds=self.horizon_seconds,
            confidence=1.0,
            reason=(f"fixed NOTOUCH barrier {barrier:.2%} over "
                    f"{self.horizon_seconds}s on {symbol or 'default'} - "
                    f"no prediction, see touch_edge scan"),
        )


class Momentum(Strategy):
    """Bets the last `lookback` candles' direction continues.

    Included to exercise the machinery and to be measured, NOT because it is
    expected to work. On a random walk the previous move says nothing about
    the next one; on a real market it sometimes does. The backtest decides.
    """

    name = "momentum"

    def __init__(self, lookback: int = 20, min_move_pct: float = 0.001,
                 horizon_seconds: int = 900):
        if lookback < 2:
            raise ValueError("lookback must be >= 2")
        if min_move_pct <= 0:
            raise ValueError("min_move_pct must be positive")
        self.lookback = lookback
        self.min_move_pct = min_move_pct
        self.horizon_seconds = horizon_seconds

    def evaluate(self, candles: Sequence[dict[str, Any]],
                symbol: str | None = None) -> Signal | None:
        if len(candles) < self.lookback:
            return None
        window = candles[-self.lookback:]
        first, last = float(window[0]["open"]), float(window[-1]["close"])
        if first <= 0:
            return None
        move = (last - first) / first
        if abs(move) < self.min_move_pct:
            return None
        return Signal(
            direction=1 if move > 0 else -1,
            expected_move_pct=abs(move),
            horizon_seconds=self.horizon_seconds,
            # Confidence grows with the move relative to the threshold, capped
            # at 1. Deliberately crude: an invented precision here would be
            # false precision.
            confidence=min(1.0, abs(move) / (self.min_move_pct * 3)),
            reason=(f"momentum: {move:+.3%} over {self.lookback} candles "
                    f"(threshold {self.min_move_pct:.3%})"),
        )


class MeanReversion(Momentum):
    """Momentum's mirror: bets the move reverses. Same caveat, same purpose -
    if both are profitable in backtest, the backtest is wrong.
    """

    name = "mean_reversion"

    def evaluate(self, candles: Sequence[dict[str, Any]],
                symbol: str | None = None) -> Signal | None:
        sig = super().evaluate(candles, symbol)
        if sig is None:
            return None
        return Signal(
            direction=-sig.direction,
            expected_move_pct=sig.expected_move_pct,
            horizon_seconds=sig.horizon_seconds,
            confidence=sig.confidence,
            reason=sig.reason.replace("momentum", "mean_reversion (inverted)"),
        )


STRATEGIES: dict[str, type[Strategy]] = {
    "never": NeverTrade,
    "momentum": Momentum,
    "mean_reversion": MeanReversion,
    "fixed_notouch": FixedNoTouch,
}


def build_strategy(name: str, **kwargs: Any) -> Strategy:
    # pdf_rise_fall lives in its own module and imports Signal from here, so
    # it is resolved lazily rather than at import time - registering it at the
    # top of this file would be a circular import.
    if name == "pdf_rise_fall":
        from .pdf_strategy import PdfRiseFall
        return PdfRiseFall(**kwargs)
    try:
        cls = STRATEGIES[name]
    except KeyError:
        choices = sorted(STRATEGIES) + ["pdf_rise_fall"]
        raise ValueError(
            f"unknown strategy '{name}' - choices: {sorted(choices)}") from None
    return cls(**kwargs)
