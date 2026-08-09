"""Verify lines.json S/R levels against Deriv's Line-chart data (raw ticks).

Deriv's "Line" chart view plots the tick stream directly - just the price at
each tick, no open/high/low/close wicks. Our strategy detects levels from
candle wicks (see pricebot/scan.py), so a level that looks solid on our
candle-based scan might not be an obvious turning point on the Line chart,
and vice versa. This script checks our current lines.json levels against the
same tick data the Line chart draws, so you can visually cross-check specific
timestamps in the browser.

For each active line, it reports:
  - touches: how many ticks came within the line's tolerance_pct
  - reversals: of those touches, how many were followed by price moving
    away from the level (a genuine bounce) rather than continuing through
  - the most recent touch timestamps, so you can jump to that point on the
    Line chart and look for yourself

Usage:
    python check_levels_on_line_chart.py [--symbol R_50] [--ticks 5000]
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

import yaml

from deriv_bot.api import DerivAPI
from pricebot.sr_lines import load_lines


def fmt(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="R_50")
    ap.add_argument("--ticks", type=int, default=5000,
                    help="how many recent ticks to pull (Deriv caps this)")
    ap.add_argument("--lines", default="lines.json")
    ap.add_argument("--reversal-window", type=int, default=20,
                    help="how many ticks ahead to look when deciding if a "
                         "touch reversed or broke through")
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
              f"timeframe) may show 0 touches simply because this window "
              f"doesn't reach back far enough - not necessarily a bad level.")
    print(f"Current price: {prices[-1]:.4f}")
    print()

    lines = [ln for ln in load_lines(args.lines) if ln.active]
    if not lines:
        print(f"No active lines in {args.lines}.")
        return

    for ln in sorted(lines, key=lambda l: l.price_level):
        tol = ln.tolerance_abs()
        touches = []
        for i, p in enumerate(prices):
            if abs(p - ln.price_level) <= tol:
                touches.append(i)

        reversals = 0
        breaks = 0
        for i in touches:
            window = prices[i + 1: i + 1 + args.reversal_window]
            if not window:
                continue
            end_price = window[-1]
            if ln.type == "support":
                # genuine bounce = price ends up HIGHER than the level
                if end_price > ln.price_level:
                    reversals += 1
                else:
                    breaks += 1
            else:
                if end_price < ln.price_level:
                    reversals += 1
                else:
                    breaks += 1

        print(f"{ln.name:3s} {ln.type:10s} @ {ln.price_level:.4f} "
              f"(+/-{ln.tolerance_pct:.3f}%, {ln.timeframe})")
        print(f"     touches in tick data: {len(touches)}  "
              f"reversals: {reversals}  breaks-through: {breaks}")
        if touches:
            recent = touches[-5:]
            print("     most recent touch timestamps (check these on the "
                  "Line chart):")
            for i in recent:
                print(f"       {fmt(times[i])} UTC  price={prices[i]:.4f}")
        else:
            print("     no ticks came within tolerance in this window - "
                  "either the level is stale/far from current price, or "
                  f"--ticks {args.ticks} does not reach back far enough")
        print()


if __name__ == "__main__":
    asyncio.run(main())
