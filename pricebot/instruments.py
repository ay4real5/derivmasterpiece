"""Turning one forecast into whichever of the three products should express it.

Pure functions returning `api.proposal` kwargs, so the entire mapping is
testable without a network - which matters because a barrier off by a factor
of 100, or a stop-loss placed outside the stop-out, is invisible in a log and
expensive live.

The three products answer different questions about the same forecast:

    MULTUP/MULTDOWN  direction, with take-profit at the expected move.
                     No expiry: the position is closed by its own orders,
                     which Deriv holds server-side. Verified live - a $3
                     stake at 400x reported take_profit, stop_loss AND
                     stop_out attached at purchase. That matters for
                     reliability: a crashed bot cannot strand a position,
                     because the exit does not depend on the bot.

    CALL/PUT         direction by a deadline. All-or-nothing at expiry, no
                     early exit, so the horizon IS the trade.

    ONETOUCH/NOTOUCH whether the move happens at all. ONETOUCH when a move is
                     expected, NOTOUCH when the forecast is that price stays
                     put - the only one of the three that can express "no
                     move" as a position rather than as inaction.

STOP-LOSS SIZING IS THE ONE THING TO GET RIGHT. Deriv stops a multiplier out
at 100% of stake regardless of what you ask for, so a stop-loss set at or
beyond the stake never triggers and the position simply runs to stop-out.
`_multiplier_orders` therefore clamps it strictly inside, and a test asserts
it.
"""
from __future__ import annotations

from typing import Any

from .signals import Signal

MULTIPLIER = "multiplier"
RISE_FALL = "rise_fall"
TOUCH = "touch"
INSTRUMENTS = (MULTIPLIER, RISE_FALL, TOUCH)

# Deriv rejects anything else; discovered from the API's own error message
# rather than the docs.
VALID_MULTIPLIERS = (400, 1000, 2000, 3000, 4000)

# Below this the forecast is "price stays put", which NOTOUCH expresses and
# the other two cannot.
FLAT_MOVE_PCT = 0.0005

# Stop-loss as a fraction of the take-profit distance. Below 1.0 so a losing
# position is cut before it reaches the full-stake stop-out.
DEFAULT_STOP_FRACTION = 0.6


def _multiplier_orders(stake: float, signal: Signal,
                       stop_fraction: float = DEFAULT_STOP_FRACTION) -> dict[str, float]:
    """take_profit and stop_loss in ACCOUNT CURRENCY, not price terms.

    Deriv wants the profit and loss amounts, and derives the trigger prices
    itself. take_profit is what the expected move is worth at this leverage;
    stop_loss is a fraction of it, clamped strictly inside the stake because
    the stop-out at 100% of stake would otherwise fire first and make the
    stop-loss decorative.
    """
    if not 0 < stop_fraction < 1:
        raise ValueError("stop_fraction must be between 0 and 1")
    take_profit = round(stake * signal.expected_move_pct * _leverage(signal), 2)
    stop_loss = round(take_profit * stop_fraction, 2)
    # Never at or beyond the stake: the stop-out owns that level.
    stop_loss = min(stop_loss, round(stake * 0.9, 2))
    return {"take_profit": max(take_profit, 0.01),
            "stop_loss": max(stop_loss, 0.01)}


def _leverage(signal: Signal) -> int:
    """Pick the smallest valid multiplier that makes the expected move
    meaningful without putting the stop-out inside normal noise.

    Higher leverage means the stop-out sits closer to spot: at 400x a 0.25%
    adverse move ends the position. So leverage is chosen from the forecast
    rather than fixed - a small expected move needs more leverage to be worth
    trading, a large one needs less and survives more noise.
    """
    if signal.expected_move_pct <= 0:
        return VALID_MULTIPLIERS[0]
    # Aim for the take-profit to be roughly 30% of stake.
    wanted = 0.30 / signal.expected_move_pct
    for m in VALID_MULTIPLIERS:
        if m >= wanted:
            return m
    return VALID_MULTIPLIERS[-1]


def build_proposal(signal: Signal, instrument: str, symbol: str, stake: float,
                   currency: str = "USD",
                   stop_fraction: float = DEFAULT_STOP_FRACTION) -> dict[str, Any] | None:
    """`api.proposal` kwargs for this signal, or None if it is not tradeable.

    Returning None rather than a zero-size order keeps "no trade" a first
    class outcome - the digit bot always traded something, which is how it
    paid the spread 80 times an hour.
    """
    if instrument not in INSTRUMENTS:
        raise ValueError(f"unknown instrument '{instrument}' - choices: {list(INSTRUMENTS)}")
    if stake <= 0:
        raise ValueError("stake must be positive")

    flat = signal.expected_move_pct < FLAT_MOVE_PCT
    if not signal.actionable and not (instrument == TOUCH and flat):
        return None

    base: dict[str, Any] = {
        "underlying_symbol": symbol,
        "amount": round(stake, 2),
        "basis": "stake",
        "currency": currency,
    }

    if instrument == MULTIPLIER:
        return {
            **base,
            "contract_type": "MULTUP" if signal.direction > 0 else "MULTDOWN",
            "multiplier": _leverage(signal),
            "limit_order": _multiplier_orders(stake, signal, stop_fraction),
        }

    if instrument == RISE_FALL:
        return {
            **base,
            "contract_type": "CALL" if signal.direction > 0 else "PUT",
            "duration": max(1, int(signal.horizon_seconds // 60)),
            "duration_unit": "m",
        }

    # TOUCH - the only instrument that can express "no move expected"
    if flat:
        contract, barrier = "NOTOUCH", f"+{signal.expected_move_pct or FLAT_MOVE_PCT:.5f}"
    else:
        contract = "ONETOUCH"
        sign = "+" if signal.direction > 0 else "-"
        barrier = f"{sign}{signal.expected_move_pct:.5f}"
    return {
        **base,
        "contract_type": contract,
        # Relative barriers are a FRACTION here, converted to a price offset
        # by the caller against spot; kept relative so the mapping stays pure.
        "barrier": barrier,
        "duration": max(2, int(signal.horizon_seconds // 60)),
        "duration_unit": "m",
    }


def stake_for(signal: Signal, base_stake: float, max_stake: float | None = None) -> float:
    """Flat stake scaled by confidence - deliberately NOT a ladder.

    The digit bot's losses exceeded its own house edge because a doubling
    ladder pushed volume into losing sequences. Multipliers have no
    settlement to step a ladder on anyway, and the position's stop-loss is
    the risk control here.
    """
    stake = base_stake * max(0.0, min(1.0, signal.confidence))
    if max_stake is not None:
        stake = min(stake, max_stake)
    return round(max(stake, 0.0), 2)
