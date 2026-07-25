"""Scans Deriv's live proposal payouts across Digits contract variants to
find which one currently carries the smallest house margin.

This does not predict outcomes. Digits are generated close to a uniform,
independent draw, so win probability is taken as the theoretical value
(1/10 per digit), not estimated from recent history. The only real, provable
lever here is *which contract prices the smallest edge against you right
now* — payout percentages vary by contract type and barrier and can drift
over time, so this is worth re-running rather than trusting a single result.

Needs an authorized session: unauthenticated `proposal` requests return
"Trading is not offered for this asset" for every contract on this API,
even though public market data (ticks_history) works without auth. Pass the
same DERIV_API_TOKEN used for `live`. Always connects via the demo account
regardless of DEMO_MODE — there's no reason a read-only payout scan needs
the real account, so it never touches it.
"""
from __future__ import annotations

from typing import Any

from .api import DerivAPI


def theoretical_win_prob(contract_type: str, barrier: str | None) -> float:
    if contract_type in ("DIGITEVEN", "DIGITODD"):
        return 0.5
    if contract_type == "DIGITMATCH":
        return 0.1
    if contract_type == "DIGITDIFF":
        return 0.9
    if contract_type in ("CALL", "PUT"):
        # Rise/Fall on a symmetric random walk: ~50% each. Approximate — an
        # exactly-flat tie loses for both sides, so true prob is a shade
        # under 0.5, meaning the edge shown for CALL/PUT is a slight
        # UNDERestimate. Good enough to rank against digit contracts.
        return 0.5
    b = int(barrier)  # type: ignore[arg-type]
    if contract_type == "DIGITOVER":
        return (9 - b) / 10
    if contract_type == "DIGITUNDER":
        return b / 10
    raise ValueError(f"unknown contract_type: {contract_type}")


def _requests() -> list[tuple[str, str | None, int]]:
    """(contract_type, barrier, duration_ticks) for every quote to request."""
    reqs: list[tuple[str, str | None, int]] = [("DIGITEVEN", None, 1), ("DIGITODD", None, 1)]
    reqs += [("DIGITOVER", str(b), 1) for b in range(0, 9)]   # barrier 9 can never win
    reqs += [("DIGITUNDER", str(b), 1) for b in range(1, 10)]  # barrier 0 can never win
    # all 10 barriers for match/differ: also verifies Deriv prices them uniformly
    reqs += [("DIGITMATCH", str(b), 1) for b in range(0, 10)]
    reqs += [("DIGITDIFF", str(b), 1) for b in range(0, 10)]
    # Rise/Fall at a few durations — pricing may differ by duration
    reqs += [("CALL", None, d) for d in (1, 5, 10)]
    reqs += [("PUT", None, d) for d in (1, 5, 10)]
    return reqs


async def scan_edge(
    app_id: str, symbol: str, stake: float, currency: str = "USD", token: str | None = None,
) -> list[dict[str, Any]]:
    if not token:
        raise ValueError("scan_edge requires a token — proposal data needs an authorized session")

    api = DerivAPI(app_id)
    accounts = await api.list_accounts(token)
    demo = next((a for a in accounts if a["account_type"] == "demo"), None)
    if demo is None:
        raise ValueError("no demo account found for this token — scan-edge only uses demo")
    ws_url = await api.request_trading_ws_url(token, demo["account_id"])
    await api.connect(ws_url)
    results: list[dict[str, Any]] = []
    try:
        for contract_type, barrier, duration in _requests():
            # the current API renamed `symbol` -> `underlying_symbol` in
            # proposal requests (tick subscriptions still use `ticks: <sym>`)
            params: dict[str, Any] = dict(
                contract_type=contract_type, underlying_symbol=symbol, amount=stake,
                basis="stake", duration=duration, duration_unit="t", currency=currency,
            )
            if barrier is not None:
                params["barrier"] = barrier
            try:
                resp = await api.proposal(**params)
            except Exception:
                continue  # e.g. contract/barrier/duration not offered right now

            details = resp["proposal"]
            payout = float(details["payout"])
            ask_price = float(details["ask_price"])
            win_prob = theoretical_win_prob(contract_type, barrier)
            ev = win_prob * payout - ask_price
            results.append({
                "contract_type": contract_type,
                "barrier": barrier,
                "duration": duration,
                "win_prob": win_prob,
                "payout": payout,
                "ask_price": ask_price,
                "ev": ev,
                "edge_pct": (-ev / ask_price * 100) if ask_price else float("nan"),
            })
    finally:
        await api.close()

    results.sort(key=lambda r: r["edge_pct"])
    return results
