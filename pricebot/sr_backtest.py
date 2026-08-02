"""Does trading bounces off higher-timeframe S/R actually beat 52%?

The proposal: draw support/resistance on 5m and 10m V50, wait for price to
reach a line, confirm on 1m candles, buy a 55-second Rise/Fall. Its own success
bar is a win rate above 55-58%; the real bar on Deriv's synthetics is 52.0%,
because Rise/Fall pays 1.9233x there rather than the 75% the proposal assumed.

This measures it before any of it gets built.

TWO THINGS THIS HAS TO GET RIGHT OR THE ANSWER IS WORTHLESS.

1. THE LINES MUST BE CAUSAL. A swing high at candle i is only recognisable
   once `k` candles have printed AFTER it - that is what makes it a swing. Code
   that scans the whole series and then "trades the bounces" is reading levels
   that were not visible at the time, and it will show a beautiful win rate
   that no live bot could ever reproduce. Every level here carries the index at
   which it BECAME KNOWN, and `active_lines` refuses to return one before then.
   `test_no_lookahead` proves it by truncating the future and checking the
   answer does not change.

2. THE LEVELS MUST BE CHOSEN BLIND. Drawing lines by hand and then testing
   them measures the hand, not the method - you remember the levels that
   worked. Swing highs and lows are generated mechanically here, which is both
   reproducible and the closest honest analogue of what an eye lands on.

Settlement is the next 1m close, a 60-second horizon against the proposal's 55.
On a feed with no measurable autocorrelation at any lag (TICK_ANALYSIS.md) that
five-second difference cannot matter, and 1m candles are the finest history
Deriv serves beyond a 24-hour tick window.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

# Deriv pays 1.9233x on synthetic Rise/Fall, so break-even is 1/1.9233.
# The proposal's `min_payout 75%` would imply 57.1% and never binds here.
PAYOUT = 1.9233
BREAK_EVEN = 1.0 / PAYOUT


def _f(c: dict[str, Any], key: str) -> float:
    return float(c[key])


def swing_levels(candles: Sequence[dict[str, Any]], k: int = 2
                 ) -> list[dict[str, Any]]:
    """Swing highs and lows, each tagged with when it BECAME KNOWN.

    A swing high at index i needs `k` candles either side to be one, so it is
    not visible until candle i+k has FINISHED.

    THE +period MATTERS AND ITS ABSENCE FAKED AN EDGE. Deriv's `epoch` is the
    candle's OPEN time - verified on 3,999 of 3,999 overlapping candles, where
    the 5m open equals the 1m open at the same epoch. So candle i+k is not
    complete until `epoch + period`, and using `candles[i+k]["epoch"]` handed
    the strategy up to a full HTF period of look-ahead: on 5m lines, five
    minutes of price action it could not have seen, against a 60-second trade.

    That alone produced a pooled 52.18% and win rates up to 62.8% at tight
    tolerances. Nothing else about the rules changed.
    """
    out: list[dict[str, Any]] = []
    n = len(candles)
    period = (int(candles[1]["epoch"]) - int(candles[0]["epoch"])) if n > 1 else 0
    for i in range(k, n - k):
        window = candles[i - k:i + k + 1]
        hi, lo = _f(candles[i], "high"), _f(candles[i], "low")
        if hi >= max(_f(c, "high") for c in window):
            out.append({"price": hi, "type": "resistance",
                        "epoch": int(candles[i]["epoch"]),
                        "known_epoch": int(candles[i + k]["epoch"]) + period})
        if lo <= min(_f(c, "low") for c in window):
            out.append({"price": lo, "type": "support",
                        "epoch": int(candles[i]["epoch"]),
                        "known_epoch": int(candles[i + k]["epoch"]) + period})
    return out


def active_lines(levels: Sequence[dict[str, Any]], now_epoch: int,
                 max_age_seconds: int = 6 * 3600) -> list[dict[str, Any]]:
    """Levels known by `now_epoch` and not yet stale.

    The age cap matters: without it every level ever formed stays live, the
    price is always within tolerance of something, and the "only trade near a
    line" restriction quietly stops restricting anything.
    """
    return [lv for lv in levels
            if lv["known_epoch"] <= now_epoch
            and now_epoch - lv["epoch"] <= max_age_seconds]


def line_broken(level: dict[str, Any], candles: Sequence[dict[str, Any]],
                since_epoch: int, now_epoch: int, tolerance_pct: float) -> bool:
    """Has price CLOSED beyond the level by more than the tolerance?

    The proposal's rule: a broken line is dead, not a trade signal. Uses closes
    rather than wicks, so a single spike through does not retire a level.
    """
    tol = level["price"] * tolerance_pct / 100.0
    for c in candles:
        e = int(c["epoch"])
        if e <= since_epoch or e > now_epoch:
            continue
        close = _f(c, "close")
        if level["type"] == "support" and close < level["price"] - tol:
            return True
        if level["type"] == "resistance" and close > level["price"] + tol:
            return True
    return False


def confirmed(candles_1m: Sequence[dict[str, Any]], i: int, want_up: bool,
              lookback: int = 3, need: int = 2) -> bool:
    """The proposal's 1m confirmation, on COMPLETED candles only.

    Candle `i` is the last completed one; `i+1` is the trade. Both conditions
    must hold: the last candle closes the right way, AND at least `need` of the
    last `lookback` do.
    """
    if i < lookback - 1:
        return False
    last = candles_1m[i]
    bull = _f(last, "close") > _f(last, "open")
    if bull != want_up:
        return False
    agree = 0
    for j in range(i - lookback + 1, i + 1):
        c = candles_1m[j]
        up = _f(c, "close") > _f(c, "open")
        if up == want_up:
            agree += 1
    return agree >= need


def annotate_breaks(levels: list[dict[str, Any]],
                    htf_candles: Sequence[dict[str, Any]],
                    tolerance_pct: float) -> None:
    """Stamp each level with the epoch it first CLOSES beyond, once.

    `line_broken` scanned every HTF candle for every level on every 1m candle -
    correct, and O(n x levels x htf), which did not finish. Same answer,
    computed once per level; `test_break_annotation_matches_line_broken` holds
    the two implementations together.
    """
    for lv in levels:
        tol = lv["price"] * tolerance_pct / 100.0
        lv["break_epoch"] = None
        for c in htf_candles:
            e = int(c["epoch"])
            if e <= lv["known_epoch"]:
                continue
            close = _f(c, "close")
            if ((lv["type"] == "support" and close < lv["price"] - tol) or
                    (lv["type"] == "resistance" and close > lv["price"] + tol)):
                lv["break_epoch"] = e
                break


def run(candles_1m: Sequence[dict[str, Any]],
        htf_candles: Sequence[dict[str, Any]],
        *, k: int = 2, tolerance_pct: float = 0.15,
        cooldown_seconds: int = 1800, max_age_seconds: int = 6 * 3600,
        require_confirmation: bool = True) -> dict[str, Any]:
    """Simulate the whole rule set. Returns counts, win rate and net.

    Entry is the CLOSE of the confirming 1m candle; settlement is the close of
    the next one. A dead-exact tie loses, matching the contract.
    """
    levels = swing_levels(htf_candles, k=k)
    annotate_breaks(levels, htf_candles, tolerance_pct)
    # Bucket by price so a 1m candle only considers levels it could be near,
    # instead of walking every level ever formed.
    trades: list[dict[str, Any]] = []
    last_fire: dict[float, int] = {}
    near_misses = 0

    for i in range(len(candles_1m) - 1):
        c = candles_1m[i]
        now = int(c["epoch"])
        price = _f(c, "close")

        for lv in active_lines(levels, now, max_age_seconds):
            tol = lv["price"] * tolerance_pct / 100.0
            if abs(price - lv["price"]) > tol:
                continue
            bk = lv.get("break_epoch")
            if bk is not None and bk <= now:
                continue
            key = round(lv["price"], 6)
            if now - last_fire.get(key, -10**9) < cooldown_seconds:
                continue

            want_up = lv["type"] == "support"
            if require_confirmation and not confirmed(candles_1m, i, want_up):
                near_misses += 1
                continue

            entry = price
            exit_ = _f(candles_1m[i + 1], "close")
            won = exit_ > entry if want_up else exit_ < entry   # tie loses
            trades.append({"epoch": now, "type": lv["type"], "won": won})
            last_fire[key] = now
            break        # one trade at a time, per the proposal

    n = len(trades)
    wins = sum(1 for t in trades if t["won"])
    rate = wins / n if n else 0.0
    net = sum((PAYOUT - 1.0) if t["won"] else -1.0 for t in trades)
    se = math.sqrt(0.25 / n) if n else 0.0
    return {
        "trades": n, "wins": wins, "win_rate": rate,
        "net_per_unit_stake": net,
        "break_even": BREAK_EVEN,
        "edge_pp": (rate - BREAK_EVEN) * 100,
        "se_pp": se * 100,
        "z_vs_break_even": ((rate - BREAK_EVEN) / se) if se else 0.0,
        "z_vs_coin_flip": ((rate - 0.5) / se) if se else 0.0,
        "levels": len(levels),
        "skipped_no_confirmation": near_misses,
    }
