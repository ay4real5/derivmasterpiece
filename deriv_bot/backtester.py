"""Replays a strategy against historical ticks with no `buy` calls.

PnL uses an approximate per-contract payout table (below), NOT the real
per-contract payout (only Deriv's `proposal` call knows that exactly, and it
drifts over time). Treat backtest PnL as directional only — use
`main.py live --dry-run` against real live proposals for an accurate payout
picture before ever trading for real.
"""
from __future__ import annotations

from typing import Any

from .api import DerivAPI
from .edge import theoretical_win_prob
from .strategy import DigitFrequencyStrategy, Signal, Strategy

# Total payout (stake included) per 1.00 staked, keyed by theoretical win
# probability. Snapshot of real R_100 `proposal` quotes (2026-07); the house
# margin baked in ranges from ~1.9% at p=0.9 to ~16.7% at p=0.1. Re-run
# `main.py scan-edge` to check for drift.
APPROX_PAYOUT_BY_WIN_PROB = {
    0.9: 1.09, 0.8: 1.22, 0.7: 1.39, 0.6: 1.61, 0.5: 1.92,
    0.4: 2.38, 0.3: 3.13, 0.2: 4.55, 0.1: 8.33,
}


def approx_net_win(signal: Signal, stake: float) -> float:
    """Approximate net profit if `signal`'s contract wins, from the payout
    table above."""
    p = round(theoretical_win_prob(signal.contract_type, signal.barrier), 1)
    return stake * (APPROX_PAYOUT_BY_WIN_PROB[p] - 1.0)


def last_digit(price: Any, pip_size: int) -> int:
    """Last digit of a quote at full pip precision. Deriv sends quotes as
    JSON numbers, so a price of 531.70 arrives as 531.7 — naive str()[-1]
    would read 7 and the digit 0 would never appear. Formatting to
    `pip_size` decimals restores the true final digit.
    """
    return int(f"{float(price):.{pip_size}f}"[-1])


def _resolves_win(signal: Signal, digit: int) -> bool:
    if signal.contract_type == "DIGITOVER":
        return digit > int(signal.barrier)
    if signal.contract_type == "DIGITUNDER":
        return digit < int(signal.barrier)
    if signal.contract_type == "DIGITEVEN":
        return digit % 2 == 0
    if signal.contract_type == "DIGITODD":
        return digit % 2 == 1
    if signal.contract_type == "DIGITMATCH":
        return digit == int(signal.barrier)
    if signal.contract_type == "DIGITDIFF":
        return digit != int(signal.barrier)
    raise ValueError(f"unknown contract_type: {signal.contract_type}")


async def fetch_ticks(app_id: str, symbol: str, count: int) -> tuple[list[Any], int]:
    """Returns (prices, pip_size) — pip_size is needed to recover true last
    digits from JSON float quotes (see `last_digit`)."""
    api = DerivAPI(app_id)
    await api.connect()
    try:
        resp = await api.ticks_history(symbol, count=count, style="ticks")
        return resp["history"]["prices"], int(resp["pip_size"])
    finally:
        await api.close()


def backtest_over_prices(
    prices: list[Any], stake: float, strategy: Strategy, pip_size: int = 2,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Feeds `strategy` one digit at a time; a signal produced after seeing
    digit[i] is resolved against digit[i+1] — the next tick, exactly like a
    real duration=1-tick contract bought right after the signal. Resolving
    against digit[i] itself (the tick that produced the signal) would be
    look-ahead bias — for a streak-based strategy it's not just biased, it's
    a guaranteed loss every time, since the triggering digit is definitionally
    on the wrong side of the barrier.
    """
    digits = [last_digit(p, pip_size) for p in prices]
    trades: list[dict[str, Any]] = []
    for i in range(len(digits) - 1):
        signal = strategy.on_tick(digits[i])
        if signal is None:
            continue
        next_digit = digits[i + 1]
        won = _resolves_win(signal, next_digit)
        profit = approx_net_win(signal, stake) if won else -stake
        trades.append({"digit": next_digit, "signal": signal, "won": won, "profit": profit})

    wins = sum(1 for t in trades if t["won"])
    report = {
        "num_trades": len(trades),
        "wins": wins,
        "win_rate": (wins / len(trades)) if trades else 0.0,
        "total_pnl": sum(t["profit"] for t in trades),
    }
    return report, trades


async def run_backtest(
    app_id: int | str,
    symbol: str,
    count: int,
    stake: float,
    strategy: Strategy | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    strategy = strategy or DigitFrequencyStrategy()
    prices, pip_size = await fetch_ticks(app_id, symbol, count)
    return backtest_over_prices(prices, stake, strategy, pip_size)
