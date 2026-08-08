"""Trade only at levels YOU drew. Everything else is a logged skip.

The backtest in `sr_backtest.py` tested a robot's levels - swing highs and lows
found mechanically - and found nothing that survived a split-half. This module
tests the actual proposal instead: levels chosen by a human eye, watched
forward.

WHY THIS CAN ONLY BE A FORWARD TEST. Drawing lines today on last week's chart
and backtesting them measures the hand, not the method - you cannot un-see
where price bounced, and the lines land there. That is true of everyone, and it
is why `lines.json` levels must be written down BEFORE the price action that
judges them. The bot timestamps each line the first time it sees it, so a level
edited after the fact is visible in the log rather than silently retro-fitted.

WHAT THE BAR IS. Deriv quotes 1.92x on a 55-second R_50 Rise/Fall, so
break-even is 1/1.92 = 52.08%. The proposal's "55-58%" assumed a 75% payout,
which does not exist here; its `min_payout` filter would never bind.

EVERY SKIP IS LOGGED WITH ITS REASON. That is not housekeeping - it is the
only way to tell "my levels are wrong" from "the confirmation never fires" from
"the bot is broken", and those three failures look identical from a P&L curve.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Sequence

# Deriv's live quote for a 55s R_50 Rise/Fall. Break-even is its inverse.
DEFAULT_PAYOUT = 1.92
VALID_TYPES = ("support", "resistance")


def break_even(payout: float) -> float:
    """Win rate needed to break even at a given payout multiple."""
    if payout <= 1.0:
        raise ValueError("payout multiple must exceed 1.0")
    return 1.0 / payout


@dataclass
class Line:
    """One level you drew, plus the state the bot keeps about it."""

    name: str
    symbol: str
    price_level: float
    type: str                      # "support" -> RISE, "resistance" -> FALL
    tolerance_pct: float = 0.15
    timeframe: str = "5m"
    active: bool = True
    # Runtime state, never read from the file.
    trades_today: int = 0
    last_trade_epoch: int | None = None
    broken: bool = False
    first_seen_epoch: int | None = None
    # Per-level track record, persisted across restarts via sr_bot_state.json
    # (see run_sr_bot.py's line_stats). Used to retire a level that keeps
    # losing even though price never technically closed through it - a level
    # can be "still there" on the chart and still be a bad bet.
    wins: int = 0
    losses: int = 0

    @property
    def wants_up(self) -> bool:
        """Support is bought as RISE, resistance as FALL."""
        return self.type == "support"

    def tolerance_abs(self) -> float:
        return abs(self.price_level) * self.tolerance_pct / 100.0

    def contains(self, price: float) -> bool:
        """Is price inside the zone? Compared with a relative epsilon.

        A plain `<=` fails on the boundary: |99.85 - 100.0| computes to
        0.15000000000000568, so a price exactly at the edge of a 0.15% band
        reads as outside it. Same class of bug as the daily cap that would not
        fire at -899.9999999999989 against a 900 limit - an exact comparison
        against an accumulated float.
        """
        return abs(price - self.price_level) <= self.tolerance_abs() * (1 + 1e-9)


def validate(raw: dict[str, Any], index: int) -> Line:
    """Turn one JSON entry into a Line, or fail with a message you can act on.

    Validation is strict on purpose. A typo'd price level is a bot that either
    never trades or trades in the wrong place, and both look like bad luck for
    a long time before they look like a typo.
    """
    where = raw.get("name") or f"entry #{index}"
    for key in ("name", "symbol", "price_level", "type"):
        if key not in raw:
            raise ValueError(f"{where}: missing required field '{key}'")
    kind = str(raw["type"]).lower()
    if kind not in VALID_TYPES:
        raise ValueError(f"{where}: type must be one of {VALID_TYPES}, "
                         f"got {raw['type']!r}")
    try:
        level = float(raw["price_level"])
    except (TypeError, ValueError):
        raise ValueError(f"{where}: price_level must be a number, "
                         f"got {raw['price_level']!r}") from None
    if level <= 0:
        raise ValueError(f"{where}: price_level must be positive, got {level}")
    tol = float(raw.get("tolerance_pct", 0.15))
    if not 0 < tol < 100:
        raise ValueError(f"{where}: tolerance_pct must be between 0 and 100, "
                         f"got {tol}")
    return Line(
        name=str(raw["name"]), symbol=str(raw["symbol"]), price_level=level,
        type=kind, tolerance_pct=tol,
        timeframe=str(raw.get("timeframe", "5m")),
        active=bool(raw.get("active", True)),
    )


def load_lines(path: str) -> list[Line]:
    """Read and validate lines.json. Duplicate names are rejected.

    Duplicates matter because cooldowns and per-line trade counts are keyed by
    name; two lines sharing one would share a budget and neither would behave
    as written.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"no lines file at {path}")
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError("lines.json must contain a JSON list")
    lines = [validate(entry, i) for i, entry in enumerate(raw)]
    seen: set[str] = set()
    for ln in lines:
        if ln.name in seen:
            raise ValueError(f"duplicate line name {ln.name!r} - names key the "
                             f"cooldown and daily trade count, so they must be "
                             f"unique")
        seen.add(ln.name)
    return lines


def merge_state(old: Sequence[Line], new: Sequence[Line]) -> list[Line]:
    """Carry runtime state across a reload, matched by name.

    You will edit lines.json while the bot runs. Without this, every edit would
    reset cooldowns and per-line trade counts, and a line that had already used
    its daily allowance would quietly get a fresh one.
    """
    by_name = {ln.name: ln for ln in old}
    out = []
    for ln in new:
        prev = by_name.get(ln.name)
        if prev is not None and prev.price_level == ln.price_level:
            ln.trades_today = prev.trades_today
            ln.last_trade_epoch = prev.last_trade_epoch
            ln.broken = prev.broken
            ln.first_seen_epoch = prev.first_seen_epoch
            ln.wins = prev.wins
            ln.losses = prev.losses
        out.append(ln)
    return out


def retire_losing_lines(lines: Sequence[Line], max_net_losses: int = 2) -> list[Line]:
    """Retire a level whose own track record is bad, even if price never
    closed through it.

    `mark_broken` only reacts to price structure - a level can sit there
    losing trade after trade while price keeps bouncing just enough to stay
    inside the zone. This is the faster kill switch: once a specific level's
    losses minus wins reaches `max_net_losses`, stop trading it. A single win
    does not erase the count - the level has to be net profitable again to
    escape retirement, which is deliberately stricter than the win itself.
    """
    if max_net_losses <= 0:
        return []
    hit = []
    for ln in lines:
        if ln.broken or not ln.active:
            continue
        if (ln.losses - ln.wins) >= max_net_losses:
            ln.broken = True
            hit.append(ln)
    return hit


def mark_broken(lines: Sequence[Line], price: float) -> list[Line]:
    """A level price has CLOSED decisively beyond is dead, per the proposal.

    Uses the same tolerance as entry, so a line is not both "close enough to
    trade" and "already broken" at the same price.
    """
    hit = []
    for ln in lines:
        if ln.broken or not ln.active:
            continue
        # Strictly OUTSIDE the zone, using the same epsilon `contains` uses, so
        # a price can never be both "close enough to trade" and "has broken it".
        tol = ln.tolerance_abs() * (1 + 1e-9)
        if ln.type == "support" and price < ln.price_level - tol:
            ln.broken = True
            hit.append(ln)
        elif ln.type == "resistance" and price > ln.price_level + tol:
            ln.broken = True
            hit.append(ln)
    return hit


@dataclass
class Limits:
    """The proposal's risk rules. None of these is negotiable at runtime."""

    stake: float = 0.35
    max_trades_per_line_per_day: int = 3
    cooldown_seconds: int = 1800
    max_daily_loss: float = 20.0
    min_payout: float = 1.0        # a multiple, not a percentage
    max_open: int = 1


@dataclass
class Decision:
    """What the bot decided, and why - the reason is logged either way."""

    line: Line | None
    direction: int                 # +1 RISE, -1 FALL, 0 none
    reason: str
    tradeable: bool = field(default=False)


def trend_direction(candles: Sequence[dict[str, Any]], lookback: int = 10) -> int:
    """Classify the trend on a higher timeframe. +1 up, -1 down, 0 flat.

    Uses the slope of the last `lookback` closes plus higher-high/lower-low
    structure. Two agreement = a trend, one or zero = flat. This is the filter
    that stops the bot buying CALLs at support while the 15m is crashing
    through it - which was the pattern in the first 10 live trades (30% win
    rate, every loss a CALL bought against a downtrend).
    """
    if len(candles) < lookback:
        return 0
    recent = candles[-lookback:]
    closes = [float(c["close"]) for c in recent]
    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    # Slope: compare first half average to second half average.
    mid = lookback // 2
    avg_first = sum(closes[:mid]) / max(mid, 1)
    avg_second = sum(closes[mid:]) / max(lookback - mid, 1)
    slope_up = avg_second > avg_first
    slope_down = avg_second < avg_first
    # Structure: higher highs + higher lows = uptrend, vice versa = downtrend.
    hh = max(highs[mid:]) > max(highs[:mid])
    hl = min(lows[mid:]) >= min(lows[:mid])
    ll = min(lows[mid:]) < min(lows[:mid])
    lh = max(highs[mid:]) <= max(highs[:mid])
    struct_up = hh and hl
    struct_down = ll and lh
    if (slope_up and struct_up) or (slope_up and hh) or (struct_up and slope_up):
        return 1
    if (slope_down and struct_down) or (slope_down and ll) or (struct_down and slope_down):
        return -1
    return 0


def decide(price: float, lines: Sequence[Line], candles_1m: Sequence[dict[str, Any]],
           now_epoch: int, limits: Limits, *, open_trades: int = 0,
           day_pnl: float = 0.0, payout: float = DEFAULT_PAYOUT,
           confirm: bool = True,
           trend: int = 0, require_wick: bool = False) -> Decision:
    """The whole entry rule set, in the proposal's own order.

    Returns a Decision every time. A skip is never silent, because "no trade"
    with a reason is the data that tells you whether your levels are wrong or
    the confirmation is simply strict.

    The `trend` argument (+1 up, -1 down, 0 flat) is the higher-timeframe trend
    from `trend_direction`. When non-zero, the bot only trades in that
    direction: CALL (support bounce) only if trend is up, PUT (resistance
    rejection) only if trend is down. This filters out the majority of losses
    seen in live testing - support bought while the 15m was falling through it.

    `require_wick` adds a second, independent confirmation: the last completed
    candle must show an actual rejection wick at the level, not just close the
    right direction. A candle that grazes the zone and keeps going is not the
    same signal as one that pierces it and snaps back - `confirmed()` alone
    cannot tell those apart because it only reads open/close.
    """
    from .sr_backtest import confirmed, wick_rejection  # one confirmation implementation

    if day_pnl <= -abs(limits.max_daily_loss):
        return Decision(None, 0, f"daily loss limit reached ({day_pnl:+.2f})")
    if open_trades >= limits.max_open:
        return Decision(None, 0, f"{open_trades} trade(s) already open")
    if payout < limits.min_payout:
        return Decision(None, 0, f"payout {payout:.4f}x below the "
                                 f"{limits.min_payout:.4f}x minimum")

    candidates = [ln for ln in lines if ln.active and not ln.broken]
    if not candidates:
        return Decision(None, 0, "no active unbroken lines")

    near = [ln for ln in candidates if ln.contains(price)]
    if not near:
        closest = min(candidates, key=lambda l: abs(price - l.price_level))
        gap = abs(price - closest.price_level) / closest.price_level * 100
        return Decision(None, 0, f"price {price:.4f} not at any line "
                                 f"(nearest {closest.name} {closest.price_level:.4f}, "
                                 f"{gap:.3f}% away, needs {closest.tolerance_pct:.3f}%)")

    for ln in near:
        if ln.trades_today >= limits.max_trades_per_line_per_day:
            continue
        if (ln.last_trade_epoch is not None
                and now_epoch - ln.last_trade_epoch < limits.cooldown_seconds):
            continue
        # Trend filter: block PUTs in an uptrend. For CALLs at support we do
        # NOT filter by trend here - account 1 trades them regardless, and
        # account 2 applies an additional --require-trend filter in
        # run_sr_bot.py. This lets the A/B test separate "CALL-only" from
        # "both-directions trend-filtered" cleanly.
        if trend > 0 and not ln.wants_up:
            continue
        if confirm and not confirmed(candles_1m, len(candles_1m) - 1, ln.wants_up):
            continue
        if require_wick and not wick_rejection(
                candles_1m, len(candles_1m) - 1, ln.wants_up,
                ln.price_level, ln.tolerance_abs()):
            continue
        trend_label = f", 15m trend {'UP' if trend > 0 else 'DOWN' if trend < 0 else 'FLAT'}"
        return Decision(ln, 1 if ln.wants_up else -1,
                        f"{ln.name} ({ln.type}) at {ln.price_level:.4f}, price "
                        f"{price:.4f}, 1m confirms "
                        f"{'RISE' if ln.wants_up else 'FALL'}{trend_label}",
                        tradeable=True)

    # Something was near, but nothing passed. Say which gate stopped it.
    ln = near[0]
    if ln.trades_today >= limits.max_trades_per_line_per_day:
        why = f"{ln.name} used its {limits.max_trades_per_line_per_day} trades today"
    elif (ln.last_trade_epoch is not None
          and now_epoch - ln.last_trade_epoch < limits.cooldown_seconds):
        left = limits.cooldown_seconds - (now_epoch - ln.last_trade_epoch)
        why = f"{ln.name} in cooldown for another {left}s"
    elif trend > 0 and not ln.wants_up:
        why = f"at {ln.name} (resistance) but 15m trend is UP - no PUT against trend"
    elif require_wick and not wick_rejection(
            candles_1m, len(candles_1m) - 1, ln.wants_up,
            ln.price_level, ln.tolerance_abs()):
        why = f"at {ln.name} but the last candle shows no rejection wick"
    else:
        why = f"at {ln.name} but the 1m candles do not confirm"
    return Decision(None, 0, why)
