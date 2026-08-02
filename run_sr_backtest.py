"""The S/R proposal, measured on real V50 history before building any of it."""
import json
from pricebot.sr_backtest import BREAK_EVEN, run

m1 = json.load(open("sr_cache/R_50_1m.json"))
htf = {"5m": json.load(open("sr_cache/R_50_5m.json")),
       "10m": json.load(open("sr_cache/R_50_10m.json"))}

print(f"1m candles: {len(m1):,}   "
      f"5m: {len(htf['5m']):,}   10m: {len(htf['10m']):,}")
print(f"break-even at Deriv's 1.9233x payout: {BREAK_EVEN*100:.2f}%\n")

print(f"{'HTF':<5}{'k':>3}{'tol%':>7}{'trades':>8}{'win%':>8}{'vs 52.0':>9}"
      f"{'SE':>7}{'z':>7}{'net/stake':>11}")
print("-" * 72)
rows = []
for tf, candles in htf.items():
    for k in (2, 3, 5):
        for tol in (0.05, 0.15, 0.30):
            r = run(m1, candles, k=k, tolerance_pct=tol)
            rows.append((tf, k, tol, r))
            if r["trades"] < 20:
                print(f"{tf:<5}{k:>3}{tol:>7.2f}{r['trades']:>8}"
                      f"{'  (too few)':>24}")
                continue
            print(f"{tf:<5}{k:>3}{tol:>7.2f}{r['trades']:>8}"
                  f"{r['win_rate']*100:>7.2f}%{r['edge_pp']:>+8.2f}pp"
                  f"{r['se_pp']:>6.2f}{r['z_vs_break_even']:>+7.2f}"
                  f"{r['net_per_unit_stake']:>+11.1f}")

usable = [r for *_ , r in rows if r["trades"] >= 20]
if usable:
    tot = sum(r["trades"] for r in usable)
    wins = sum(r["wins"] for r in usable)
    print(f"\nPOOLED across every variant: {tot:,} trades, "
          f"{wins/tot*100:.2f}% win rate")
    import math
    se = math.sqrt(0.25/tot)*100
    print(f"  SE {se:.2f}pp   vs break-even 51.99%: "
          f"{(wins/tot-BREAK_EVEN)/ (se/100):+.2f} SE")
    print(f"  vs a coin flip 50.00%: {(wins/tot-0.5)/(se/100):+.2f} SE")

# The control: same trades, confirmation switched off.
print("\nCONTROL - does the 1m confirmation add anything?")
for tf, candles in htf.items():
    a = run(m1, candles, k=3, tolerance_pct=0.15, require_confirmation=True)
    b = run(m1, candles, k=3, tolerance_pct=0.15, require_confirmation=False)
    print(f"  {tf}: with {a['win_rate']*100:.2f}% ({a['trades']} trades)   "
          f"without {b['win_rate']*100:.2f}% ({b['trades']} trades)")
