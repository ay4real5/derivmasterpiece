"""Replays a strategy against historical ticks with no `buy` calls.

The PnL figure uses a flat approximate payout multiplier, NOT the real
per-contract payout (that depends on barrier/duration and only Deriv's
`proposal` call knows it exactly). Treat backtest PnL as directional only —
use `main.py live --dry-run` against real live proposals for an accurate
payout picture before ever trading for real.
"""
from __future__ import annotations

from typing import Any

from .api import DerivAPI
from .strategy import DigitFrequencyStrategy, Signal, Strategy

APPROX_PAYOUT_MULTIPLIER = 0.9  # rough digit-contract payout; see module docstring


def _last_digit(price: Any) -> int:
    return int(str(price)[-1])


def _resolves_win(signal: Signal, digit: int) -> bool:
    if signal.contract_type == "DIGITOVER":
        return digit > int(signal.barrier)
    if signal.contract_type == "DIGITUNDER":
        return digit < int(signal.barrier)
    if signal.contract_type == "DIGITEVEN":
        return digit % 2 == 0
    if signal.contract_type == "DIGITODD":
        return digit % 2 == 1
    raise ValueError(f"unknown contract_type: {signal.contract_type}")


async def run_backtest(
    app_id: int | str,
    symbol: str,
    count: int,
    stake: float,
    strategy: Strategy | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    strategy = strategy or DigitFrequencyStrategy()

    api = DerivAPI(app_id)
    await api.connect()
    try:
        resp = await api.ticks_history(symbol, count=count, style="ticks")
        prices = resp["history"]["prices"]
    finally:
        await api.close()

    trades: list[dict[str, Any]] = []
    for price in prices:
        digit = _last_digit(price)
        signal = strategy.on_tick(digit)
        if signal is None:
            continue
        won = _resolves_win(signal, digit)
        profit = stake * APPROX_PAYOUT_MULTIPLIER if won else -stake
        trades.append({"digit": digit, "signal": signal, "won": won, "profit": profit})

    wins = sum(1 for t in trades if t["won"])
    report = {
        "num_trades": len(trades),
        "wins": wins,
        "win_rate": (wins / len(trades)) if trades else 0.0,
        "total_pnl": sum(t["profit"] for t in trades),
    }
    return report, trades
