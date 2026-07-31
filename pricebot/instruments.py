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

# Which `contract_type` values each instrument actually places - used by
# `pricebot/reconcile.py` to scope profit_table reconciliation to one bot's
# own trades on an account that may run more than one bot at once.
CONTRACT_TYPES_FOR_INSTRUMENT: dict[str, set[str]] = {
    MULTIPLIER: {"MULTUP", "MULTDOWN"},
    RISE_FALL: {"CALL", "PUT"},
    TOUCH: {"ONETOUCH", "NOTOUCH"},
}

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
                       stop_fraction: float = DEFAULT_STOP_FRACTION,
                       multiplier: float | None = None) -> dict[str, float]:
    """take_profit and stop_loss in ACCOUNT CURRENCY, not price terms.

    Deriv wants the profit and loss amounts, and derives the trigger prices
    itself. take_profit is what the expected move is worth at this leverage;
    stop_loss is a fraction of it, clamped strictly inside the stake because
    the stop-out at 100% of stake would otherwise fire first and make the
    stop-loss decorative.
    """
    if not 0 < stop_fraction < 1:
        raise ValueError("stop_fraction must be between 0 and 1")
    lev = multiplier if multiplier else _leverage(signal)
    take_profit = round(stake * signal.expected_move_pct * lev, 2)
    stop_loss = round(take_profit * stop_fraction, 2)
    # Never at or beyond the stake: the stop-out owns that level.
    stop_loss = min(stop_loss, round(stake * 0.9, 2))
    return {"take_profit": max(take_profit, 0.01),
            "stop_loss": max(stop_loss, 0.01)}


def _leverage(signal: Signal, stake: float = 1.0,
              allowed: tuple[int, ...] | None = None,
              commission: float = 0.0,
              min_reward_multiple: float = 3.0) -> int:
    """Smallest allowed multiplier whose take-profit still beats the fee.

    `allowed` MUST come from the symbol. Deriv's ranges are per-instrument
    and not similar: R_10 takes [400, 1000, 2000, 3000, 4000], R_25 takes
    [160, 400, ...], R_100 takes [40, 100, ...], and every real market takes
    [100, 200, 300, 500, 800]. A hardcoded tuple silently fitted only R_10 -
    gold was rejected on every attempt of a live session and never traded
    once, while R_25 worked by coincidence because 400 appears in both lists.

    SMALLEST, not largest, is the point. Cost per day scales with the
    multiplier SQUARED, because leverage pulls the stop-out closer and the
    position dies and reopens more often. The only reason to go higher is
    that a take-profit worth less than the commission cannot pay for itself,
    so this climbs only until the reward clears `min_reward_multiple` fees,
    then stops.
    """
    # None means "not supplied, use the synthetic defaults"; an EMPTY tuple
    # means "this symbol offers none" and must fail loudly. Treating the two
    # the same is what let a hardcoded synthetic range be applied to gold.
    options = VALID_MULTIPLIERS if allowed is None else tuple(allowed)
    if not options:
        raise ValueError("no allowed multipliers for this symbol")
    options = tuple(sorted(options))
    if signal.expected_move_pct <= 0 or commission <= 0:
        return options[0]
    for m in options:
        if stake * signal.expected_move_pct * m >= min_reward_multiple * commission:
            return m
    return options[-1]


def build_proposal(signal: Signal, instrument: str, symbol: str, stake: float,
                   currency: str = "USD",
                   stop_fraction: float = DEFAULT_STOP_FRACTION,
                   allowed_multipliers: tuple[int, ...] | None = None,
                   commission: float = 0.0,
                   duration: int | None = None,
                   duration_unit: str | None = None) -> dict[str, Any] | None:
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
    # direction == 0 with a POSITIVE move is a second, distinct "no forecast"
    # case from `flat`: not "price will stay within a hair of here" but "no
    # view on direction, price this barrier's win-rate shape" - e.g. a
    # strategy that buys NOTOUCH at a deliberately WIDE barrier purely for
    # its margin (see deriv_bot/touch_edge.py). Both collapse to the same
    # NOTOUCH branch below; `flat` alone is kept for the narrow case so a
    # directional strategy with a near-zero move still gets NOTOUCH, exactly
    # as before this was added.
    no_view = signal.direction == 0 and signal.expected_move_pct > 0
    if not signal.actionable and not (instrument == TOUCH and (flat or no_view)):
        return None

    base: dict[str, Any] = {
        "underlying_symbol": symbol,
        "amount": round(stake, 2),
        "basis": "stake",
        "currency": currency,
    }

    if instrument == MULTIPLIER:
        mult = _leverage(signal, stake, allowed_multipliers, commission)
        return {
            **base,
            "contract_type": "MULTUP" if signal.direction > 0 else "MULTDOWN",
            "multiplier": mult,
            "limit_order": _multiplier_orders(stake, signal, stop_fraction, mult),
        }

    if instrument == RISE_FALL:
        # An explicit duration wins over the signal's horizon. Tick contracts
        # cannot be expressed in seconds - "3 ticks" is 6 seconds on an R_
        # symbol and 3 on a 1HZ one - so deriving them from horizon_seconds
        # would silently mean different things per symbol.
        if duration is not None:
            if duration < 1:
                raise ValueError("duration must be >= 1")
            unit = duration_unit or "m"
            if unit not in ("t", "s", "m", "h", "d"):
                raise ValueError(f"bad duration_unit {unit!r}")
            dur, unit_out = int(duration), unit
        else:
            dur, unit_out = max(1, int(signal.horizon_seconds // 60)), "m"
        return {
            **base,
            "contract_type": "CALL" if signal.direction > 0 else "PUT",
            "duration": dur,
            "duration_unit": unit_out,
        }

    # TOUCH - the only instrument that can express "no move expected"
    #
    # 4 DECIMAL PLACES, NOT 5. Deriv rejects a relative barrier with a 5th
    # decimal ("Barrier can only be up to 4 decimal places") - discovered
    # live the first time this path was ever exercised end to end (no config
    # had set instrument: touch before deriv_bot/touch_edge.py's scan and
    # this bot). Every quoted barrier in that scan and in config.notouch.yaml
    # is at least 0.0001 wide, so 4 places loses no precision that mattered.
    if flat or no_view:
        contract, barrier = "NOTOUCH", f"+{signal.expected_move_pct or FLAT_MOVE_PCT:.4f}"
    else:
        contract = "ONETOUCH"
        sign = "+" if signal.direction > 0 else "-"
        barrier = f"{sign}{signal.expected_move_pct:.4f}"
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
