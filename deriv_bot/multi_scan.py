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

from fractions import Fraction
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
# barrier 4 (not the cheapest-possible barrier — a deliberate choice to
# compare a real mid-tier bet, not just default to the safest one), Even/
# Odd, and Rise/Fall. The scanner still picks whichever is cheapest per
# symbol per cycle; nothing here is a recommendation, just the menu.
DEFAULT_CANDIDATES: list[tuple[str, str | None]] = [
    ("DIGITOVER", "4"), ("DIGITUNDER", "4"),
    ("DIGITEVEN", None), ("DIGITODD", None),
    ("CALL", None), ("PUT", None),
]


# Which (contract_type, barrier) legs belong to each of the three
# categories — used to force interchange between categories (see
# `RoundRobin`) rather than letting a single greedy "cheapest overall" pick
# starve two of the three every time.
CATEGORY_LEGS: dict[str, list[tuple[str, str | None]]] = {
    "over_under": [("DIGITOVER", "4"), ("DIGITUNDER", "4")],
    "even_odd": [("DIGITEVEN", None), ("DIGITODD", None)],
    "rise_fall": [("CALL", None), ("PUT", None)],
    # ONE-SIDED categories. A two-leg category lets the scan pick whichever
    # side is cheaper this cycle; a one-leg category commits to a side and
    # takes it every time its turn comes up.
    #
    # These exist to make "always bet EVEN, never ODD" expressible in config
    # rather than requiring a code change. They are listed AFTER the two-sided
    # entries deliberately: the reverse lookup at main.py's `category = next(
    # (c for c, legs in CATEGORY_LEGS.items() if ...))` returns the first
    # match, and DIGITEVEN belongs to both `even_odd` and `even`, so ordering
    # keeps that display naming unchanged for existing configs.
    #
    # Worth being clear about what a one-sided category does and does not do.
    # It does NOT change the odds: DIGITEVEN wins on 0/2/4/6/8, five of ten,
    # and CALL wins on a coin flip. Both are ~50% bets at their quoted margin,
    # exactly as the two-sided versions are. What it changes is that the bot
    # can no longer switch sides mid-run, so any streak in the underlying is
    # ridden rather than straddled.
    "even": [("DIGITEVEN", None)],
    "odd": [("DIGITODD", None)],
    "rise": [("CALL", None)],
    "fall": [("PUT", None)],
    "over": [("DIGITOVER", "4")],
    "under": [("DIGITUNDER", "4")],
}

# The one-sided entries, for tests and tooling that need to tell them apart.
ONE_SIDED_CATEGORIES = frozenset(
    c for c, legs in CATEGORY_LEGS.items() if len(legs) == 1)


class RoundRobin:
    """Weighted round-robin scheduler (credit-based, same mechanism as
    `QuotaRotationStrategy`): every call to `next()` advances each item's
    credit by its share of 1.0 and returns whichever item has the most
    credit, deducting 1 from it. With equal shares this produces a plain
    repeating cycle through all items in order — guaranteed full coverage,
    not "whichever happens to be cheapest" (which can starve every option
    but one forever, exactly what was observed: scan-trade greedily picking
    the same symbol/contract every single cycle).

    Credit is kept as exact `Fraction`s, not floats: with float division,
    equal thirds (1/3 + 1/3 + 1/3) don't sum back to exactly the starting
    point after a full cycle — binary floats can't represent 1/3 exactly —
    so two credits that should be perfectly tied at a cycle boundary drift
    apart by ~1e-16, and `max()`'s strict comparison then picks whichever
    epsilon happens to be larger, silently breaking the cycle (confirmed:
    a plain 3-item equal-weight rotation produced `a,b,c,c,a,b,c,a,b`
    instead of `a,b,c` repeating). Exact rational arithmetic has no
    rounding error, so ties stay exactly tied forever."""

    def __init__(self, items: list[Any], weights: list[float] | None = None):
        if not items:
            raise ValueError("RoundRobin needs at least one item")
        self.items = list(items)
        n = len(self.items)
        w = [Fraction(x).limit_denominator(10**9) for x in (weights or [1] * n)]
        total = sum(w)
        self.weights = [x / total for x in w]
        self.credit = [Fraction(0)] * n

    def next(self) -> Any:
        for i in range(len(self.credit)):
            self.credit[i] += self.weights[i]
        idx = max(range(len(self.credit)), key=lambda i: self.credit[i])
        self.credit[idx] -= 1
        return self.items[idx]


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
    stake: float, currency: str, errors: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Queries every (symbol, contract) combination on the already-connected
    `api` and returns all successful quotes sorted by edge_pct ascending
    (cheapest first). Failures (contract not offered on that symbol right
    now, etc.) are skipped — one failing combination is normal.

    ALL of them failing is not. Pass `errors` to collect the reasons: a dead
    websocket, an expired session or a rate limit makes every quote raise,
    the caller sees only an empty list, and without this the real cause is
    discarded. That produced a silent 18-minute stall — the process alive
    and logging "scan returned no quotes" every cycle, never trading and
    never failing loudly enough for the supervisor to restart it.
    """
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
            except Exception as exc:  # noqa: BLE001 — one bad combo must not stop the scan
                if errors is not None:
                    label = contract_type + ("" if barrier is None else f":{barrier}")
                    errors.append(f"{symbol} {label}: {type(exc).__name__}: {exc}")
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
