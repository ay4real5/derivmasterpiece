"""Does a level tested MORE TIMES actually win more often?

That is the part of "draw support and resistance" a swing detector misses, and
the only untested piece of the proposal. If a 3-touch level beats a 1-touch
level, level strength is real and worth building on. If they are the same,
strength is a story we tell about noise.
"""
import json, math
from pricebot.sr_backtest import BREAK_EVEN, run_clustered

m1 = json.load(open("sr_cache/R_50_1m.json"))
htf = {"5m": json.load(open("sr_cache/R_50_5m.json")),
       "10m": json.load(open("sr_cache/R_50_10m.json"))}
print(f"break-even {BREAK_EVEN*100:.2f}%   1m candles {len(m1):,}\n")
print(f"{'HTF':<5}{'touches':>9}{'levels':>8}{'trades':>8}{'win%':>8}"
      f"{'vs 52.0':>9}{'SE':>7}{'z':>7}")
print("-"*62)
by_touch = {}
for tf, cs in htf.items():
    for mt in (1, 2, 3, 4):
        r = run_clustered(m1, cs, k=2, tolerance_pct=0.15, min_touches=mt)
        by_touch.setdefault(mt, [0, 0])
        by_touch[mt][0] += r["trades"]; by_touch[mt][1] += r["wins"]
        if r["trades"] < 20:
            print(f"{tf:<5}{mt:>9}{r['levels']:>8}{r['trades']:>8}    (too few)")
            continue
        print(f"{tf:<5}{mt:>9}{r['levels']:>8}{r['trades']:>8}"
              f"{r['win_rate']*100:>7.2f}%{(r['win_rate']-BREAK_EVEN)*100:>+8.2f}pp"
              f"{r['se_pp']:>6.2f}{r['z_vs_break_even']:>+7.2f}")

print("\nDoes win rate RISE with touches? (both timeframes pooled)")
print(f"{'touches':>9}{'trades':>9}{'win%':>9}")
prev = None
for mt in sorted(by_touch):
    t, w = by_touch[mt]
    if t < 20:
        print(f"{mt:>9}{t:>9}   (too few)"); continue
    print(f"{mt:>9}{t:>9}{w/t*100:>8.2f}%")
    prev = w/t

print("""
If strength were real, the win rate would climb with the touch count. A flat or
random column means a level tested three times is no different from one tested
once - which is what a random walk predicts, since 'touches' there is just how
often noise happened to revisit a price.""")
