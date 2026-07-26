"""Multi-symbol, multi-contract scan-and-trade.

Instead of watching one symbol's tick stream and reacting to digit
patterns, this periodically queries REAL live payouts across several
symbols and contract types, and always trades whichever combination
currently has the smallest house margin.

This is only honest because digit outcomes carry no usable information
(see the uniformity/independence tests elsewhere in this repo) — there is
nothing to "watch" a tick stream FOR. The one genuine, provable lever is
picking the cheapest bet available right now, and that demonstrably varies
by symbol: measured live, R_100 prices ~60% more margin than R_10/25/50/75
for the identical DIGITOVER 0 contract. This module generalizes that
scan-edge check across symbols instead of assuming any one of them.
"""
from __future__ import annotations

from typing import Any

from .edge import theoretical_win_prob
from .strategy import LowEdgeStrategy

# Both volatility index families: the classic ~2s-tick indices and their
# 1-second-tick counterparts. Verified live that both families' symbols
# resolve (2026-07): R_10/25/50/75/100 and 1HZ10V/25V/50V/75V/100V.
DEFAULT_SYMBOLS = [
    "R_10", "R_25", "R_50", "R_75", "R_100",
    "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",
]

# Exactly three contract categories to interchange between: Over/Under at
# barrier 3 (not the cheapest-possible barrier — a deliberate choice to
# compare a real mid-tier bet, not just default to the safest one), Even/
# Odd, and Rise/Fall. The scanner still picks whichever is cheapest per
# symbol per cycle; nothing here is a recommendation, just the menu.
DEFAULT_CANDIDATES: list[tuple[str, str | None]] = [
    ("DIGITOVER", "3"), ("DIGITUNDER", "3"),
    ("DIGITEVEN", None), ("DIGITODD", None),
    ("CALL", None), ("PUT", None),
]


def parse_candidate_specs(specs: list[str]) -> list[tuple[str, str | None]]:
    """Parses ["DIGITOVER:0", "DIGITEVEN", "CALL"] style config entries."""
    out: list[tuple[str, str | None]] = []
    for spec in specs:
        kind, _, barrier = str(spec).partition(":")
        kind = kind.strip().upper()
        if kind not in LowEdgeStrategy.CONTRACT_TYPES:
            raise ValueError(f"unknown contract_type {kind!r}")
        if kind in LowEdgeStrategy._NO_BARRIER:
            out.append((kind, None))
        else:
            if barrier == "" or not 0 <= int(barrier) <= 9:
                raise ValueError(f"{kind} needs a barrier 0-9, e.g. {kind}:0")
            out.append((kind, str(int(barrier))))
    return out


async def scan_best(
    api: Any, symbols: list[str], candidates: list[tuple[str, str | None]],
    stake: float, currency: str,
) -> list[dict[str, Any]]:
    """Queries every (symbol, contract) combination on the already-connected
    `api` and returns all successful quotes sorted by edge_pct ascending
    (cheapest first). Failures (contract not offered on that symbol right
    now, etc.) are skipped silently — that's normal, not an error."""
    results: list[dict[str, Any]] = []
    for symbol in symbols:
        for contract_type, barrier in candidates:
            params: dict[str, Any] = dict(
                contract_type=contract_type, underlying_symbol=symbol, amount=stake,
                basis="stake", duration=1, duration_unit="t", currency=currency,
            )
            if barrier is not None:
                params["barrier"] = barrier
            try:
                resp = await api.proposal(**params)
            except Exception:
                continue
            details = resp["proposal"]
            payout = float(details["payout"])
            ask_price = float(details["ask_price"])
            win_prob = theoretical_win_prob(contract_type, barrier)
            ev = win_prob * payout - ask_price
            results.append({
                "symbol": symbol,
                "contract_type": contract_type,
                "barrier": barrier,
                "win_prob": win_prob,
                "payout": payout,
                "ask_price": ask_price,
                "ev": ev,
                "edge_pct": (-ev / ask_price * 100) if ask_price else float("nan"),
            })
    results.sort(key=lambda r: r["edge_pct"])
    return results
