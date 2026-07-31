"""Scans Deriv's live ONETOUCH/NOTOUCH payouts across barriers and durations,
model-free, the same way `edge.py` scans Digits.

WHY MODEL-FREE. Unlike a digit (theoretical win probability = 1/10 per
value), a Touch/No Touch win probability depends on the symbol's volatility
and cannot be assumed. But ONETOUCH and NOTOUCH at the SAME barrier and
duration are complementary — exactly one of them pays — so

    margin = stake/payout_touch + stake/payout_no_touch - 1

is the house margin with no volatility model, no distribution, no barrier
mathematics, exactly like the DIGITEVEN/DIGITODD pair trick in `edge.py`'s
sibling reports (see REAL_MARKETS.md). `notouch_win_prob` below is the
IMPLIED probability from the quote (stake/payout, zero-margin approximation),
reported for barrier selection, not claimed as the true probability.

THIS DOES NOT FIND AN EDGE. Confirmed by TICK_ANALYSIS.md's 260 tests: these
feeds have no exploitable structure, so a wider barrier only trades a higher
win-rate for a smaller payout at THE SAME expected value — a payout shape you
can buy, not a forecast. This scan exists to quantify that shape (and its
cost) before picking one, not to claim it beats the margin.

Needs an authorized session, same as `edge.py::scan_edge` — always the demo
account regardless of DEMO_MODE, since this is a read-only payout scan.
"""
from __future__ import annotations

from typing import Any, Sequence

from .api import DerivAPI

# Fractions of spot price. Kept well inside Deriv's quotable range — for
# reference, REAL_MARKETS.md found ~0.15x daily volatility as roughly the
# tightest barrier Deriv will quote on a 1-day Touch; these are the
# minute/hour-scale equivalents and may not all be offered on every symbol —
# a barrier Deriv refuses to quote is skipped, not treated as an error.
DEFAULT_BARRIER_PCTS: tuple[float, ...] = (
    0.001, 0.002, 0.003, 0.005, 0.01, 0.02, 0.03, 0.05, 0.10, 0.15, 0.20, 0.30,
)

# (duration, duration_unit) pairs spanning the "5m-2h" range this repo's own
# measured-costs table (README.md/OPERATING_STATE.md) found cheapest for
# Touch/No Touch (2.31-2.52%), well clear of the 5-10 TICK band it found
# worst (6.52%).
DEFAULT_DURATIONS: tuple[tuple[int, str], ...] = (
    (5, "m"), (15, "m"), (30, "m"), (60, "m"), (120, "m"),
)


def margin_and_win_prob(stake: float, touch_payout: float, no_touch_payout: float) -> tuple[float, float]:
    """(margin, notouch_win_prob_implied), both as fractions of 1.

    Pure so the arithmetic is testable without a network call: see the
    module docstring for why the complementary-pair trick needs no
    volatility model at all.
    """
    if stake <= 0 or touch_payout <= 0 or no_touch_payout <= 0:
        raise ValueError("stake and both payouts must be positive")
    touch_prob_implied = stake / touch_payout
    no_touch_prob_implied = stake / no_touch_payout
    margin = touch_prob_implied + no_touch_prob_implied - 1
    return margin, no_touch_prob_implied


async def scan_touch(
    app_id: str, symbols: Sequence[str], stake: float, *,
    barrier_pcts: Sequence[float] = DEFAULT_BARRIER_PCTS,
    durations: Sequence[tuple[int, str]] = DEFAULT_DURATIONS,
    currency: str = "USD", token: str | None = None,
) -> list[dict[str, Any]]:
    if not token:
        raise ValueError("scan_touch requires a token — proposal data needs an authorized session")

    api = DerivAPI(app_id)
    accounts = await api.list_accounts(token)
    demo = next((a for a in accounts if a["account_type"] == "demo"), None)
    if demo is None:
        raise ValueError("no demo account found for this token — scan-touch only uses demo")
    ws_url = await api.request_trading_ws_url(token, demo["account_id"])
    await api.connect(ws_url)
    results: list[dict[str, Any]] = []
    try:
        for symbol in symbols:
            for duration, unit in durations:
                for pct in barrier_pcts:
                    barrier = f"+{pct:.5f}"
                    base = dict(
                        underlying_symbol=symbol, amount=stake, basis="stake",
                        duration=duration, duration_unit=unit, currency=currency,
                        barrier=barrier,
                    )
                    try:
                        touch_resp = await api.proposal(contract_type="ONETOUCH", **base)
                        no_touch_resp = await api.proposal(contract_type="NOTOUCH", **base)
                    except Exception:
                        continue  # barrier/duration not offered right now on this symbol

                    touch_payout = float(touch_resp["proposal"]["payout"])
                    no_touch_payout = float(no_touch_resp["proposal"]["payout"])
                    if touch_payout <= 0 or no_touch_payout <= 0:
                        continue

                    margin, no_touch_prob_implied = margin_and_win_prob(
                        stake, touch_payout, no_touch_payout)
                    results.append({
                        "symbol": symbol,
                        "duration": duration,
                        "duration_unit": unit,
                        "barrier_pct": pct,
                        "touch_payout": touch_payout,
                        "no_touch_payout": no_touch_payout,
                        "margin_pct": margin * 100,
                        "notouch_win_prob_pct": no_touch_prob_implied * 100,
                    })
    finally:
        await api.close()

    results.sort(key=lambda r: (r["symbol"], r["duration"], r["barrier_pct"]))
    return results
