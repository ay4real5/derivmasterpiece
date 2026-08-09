"""Verify lines.json S/R levels against Deriv's Line-chart data (raw ticks).

Deriv's "Line" chart view plots the tick stream directly - just the price at
each tick, no open/high/low/close wicks. Our strategy detects levels from
candle wicks (see pricebot/scan.py), so a level that looks solid on our
candle-based scan might not be an obvious turning point on the Line chart,
and vice versa. This script checks our current lines.json levels against the
same tick data the Line chart draws, so you can visually cross-check specific
timestamps in the browser.

EPISODES, NOT RAW TICKS. Counting every tick that lands inside a level's
tolerance band massively overstates how many times a level was actually
"tested". Ticks arrive every ~2 seconds; if price spends five minutes slowly
grinding through a zone, that is ONE event, not ~150 independent approaches.
The first version of this script counted per-tick and reported R1 as
"685 touches, 681 breaks" - 68.5% of every tick pulled in a 33-minute window
was "in tolerance", which is a sign of one sustained move through the zone,
not 685 separate rejections. This version groups consecutive in-tolerance
ticks into a single "episode" and classifies each episode once, based on
which side price exited the zone on (or "still inside" if the data ran out
before it exited).

For each active line, it reports:
  - episodes: distinct visits to the zone (grouped, not per-tick)
  - reversals: episodes that exited on the SAME side they entered (bounce)
  - breaks: episodes that exited on the OPPOSITE side (level failed)
  - still-inside: episodes where price was still in the zone when the tick
    window ran out (neither reversal nor break yet)
  - the raw per-tick count too, for comparison against the old method

Usage:
    python check_levels_on_line_chart.py [--symbol R_50] [--ticks 5000]
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import yaml

from deriv_bot.api import DerivAPI
from pricebot.sr_lines import load_lines


def fmt(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Episode:
    start_idx: int
    end_idx: int
    entry_side: str    # "below" or "above" the level
    exit_side: str | None  # None if still inside when data ran out


def find_episodes(prices: list[float], level: float, tol: float) -> list[Episode]:
    """Group consecutive in-tolerance ticks into single touch episodes.

    entry_side is which side of the level price was on the tick BEFORE it
    entered the zone (or the first tick, if the series starts inside it).
    exit_side is which side it was on the tick AFTER it left the zone, or
    None if the series ends while price is still inside.
    """
    episodes: list[Episode] = []
    in_zone = False
    start_idx = None
    entry_side = None

    def side_of(p: float) -> str:
        return "below" if p < level else "above"

    for i, p in enumerate(prices):
        inside = abs(p - level) <= tol
        if inside and not in_zone:
            in_zone = True
            start_idx = i
            entry_side = side_of(prices[i - 1]) if i > 0 else side_of(p)
        elif not inside and in_zone:
            in_zone = False
            exit_side = side_of(p)
            episodes.append(Episode(start_idx, i - 1, entry_side, exit_side))
    if in_zone:
        episodes.append(Episode(start_idx, len(prices) - 1, entry_side, None))
    return episodes


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="R_50")
    ap.add_argument("--ticks", type=int, default=5000,
                    help="how many recent ticks to pull (Deriv caps this)")
    ap.add_argument("--lines", default="lines.json")
    args = ap.parse_args()

    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    api = DerivAPI(cfg["app_id"])
    await api.connect()  # public endpoint, no auth needed for tick history

    try:
        resp = await api.ticks_history(args.symbol, count=args.ticks, style="ticks")
        history = resp["history"]
        prices = [float(p) for p in history["prices"]]
        times = [int(t) for t in history["times"]]
    finally:
        await api.close()

    if not prices:
        print("No tick data returned - check symbol/connection.")
        return

    print(f"Pulled {len(prices)} ticks for {args.symbol}, "
          f"from {fmt(times[0])} to {fmt(times[-1])} UTC")
    if len(prices) < args.ticks:
        span_min = (times[-1] - times[0]) / 60
        print(f"NOTE: Deriv's ticks_history caps at {len(prices)} ticks per "
              f"request regardless of --ticks; that covers about "
              f"{span_min:.0f} minutes here. Older levels (5m/10m/1H "
              f"timeframe) may show 0 episodes simply because this window "
              f"doesn't reach back far enough - not necessarily a bad level.")
    print(f"Current price: {prices[-1]:.4f}")
    print()

    lines = [ln for ln in load_lines(args.lines) if ln.active]
    if not lines:
        print(f"No active lines in {args.lines}.")
        return

    for ln in sorted(lines, key=lambda l: l.price_level):
        tol = ln.tolerance_abs()
        raw_touch_count = sum(1 for p in prices if abs(p - ln.price_level) <= tol)
        episodes = find_episodes(prices, ln.price_level, tol)

        reversals = sum(1 for e in episodes if e.exit_side == e.entry_side)
        breaks = sum(1 for e in episodes
                     if e.exit_side is not None and e.exit_side != e.entry_side)
        still_inside = sum(1 for e in episodes if e.exit_side is None)

        print(f"{ln.name:3s} {ln.type:10s} @ {ln.price_level:.4f} "
              f"(+/-{ln.tolerance_pct:.3f}%, {ln.timeframe})")
        print(f"     raw in-tolerance ticks: {raw_touch_count}  "
              f"(old per-tick method would have reported this as \"touches\")")
        print(f"     distinct episodes: {len(episodes)}  "
              f"reversals: {reversals}  breaks-through: {breaks}  "
              f"still-inside-at-end: {still_inside}")
        if episodes:
            recent = episodes[-5:]
            print("     most recent episodes (check these on the Line chart):")
            for e in recent:
                dur = times[e.end_idx] - times[e.start_idx]
                outcome = e.exit_side if e.exit_side else "still inside"
                print(f"       {fmt(times[e.start_idx])} -> {fmt(times[e.end_idx])} UTC "
                      f"({dur}s)  entered from {e.entry_side}, exited {outcome}")
        else:
            print("     no episodes in this window - either the level is "
                  "stale/far from current price, or "
                  f"--ticks {args.ticks} does not reach back far enough")
        print()


if __name__ == "__main__":
    asyncio.run(main())

