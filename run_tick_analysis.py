"""Does Deriv's synthetic tick generator deviate from a random walk?

The question that decides whether any strategy can work. Runs the full
battery on raw ticks and reports every result against a Bonferroni-corrected
threshold, because a search over ~200 tests finds "significant" results in
pure noise by construction.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import yaml
from dotenv import load_dotenv

from deriv_bot.api import DerivAPI
from pricebot.tick_stats import (
    ALPHA,
    autocorrelation,
    cross_correlation,
    hourly_direction,
    hourly_volatility,
    ljung_box,
    returns,
    runs_test,
    streak_continuation,
    summarise,
    volatility_clustering,
)

FAMILIES = {
    # 1-second indices - the ones the digit bot traded
    "1HZ": ["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V"],
    # 2-second indices - same generator, half the tick rate, so the same
    # tick count covers twice the wall-clock span
    "R": ["R_10", "R_25", "R_50", "R_75", "R_100"],
}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=100000)
    ap.add_argument("--out", default="tick_analysis.json")
    ap.add_argument("--family", choices=sorted(FAMILIES), default="1HZ")
    ap.add_argument("--cache", default="tick_cache")
    args = ap.parse_args()

    load_dotenv()
    token = os.environ["DERIV_API_TOKEN"]
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    api = DerivAPI(cfg["app_id"])
    acct = next(a for a in await api.list_accounts(token)
                if a.get("account_type") == "demo")
    await api.connect(await api.request_trading_ws_url(token, acct["account_id"]))

    symbols = FAMILIES[args.family]
    os.makedirs(args.cache, exist_ok=True)
    all_results, report, series = [], {}, {}
    try:
        for sym in symbols:
            # Cache raw ticks so re-analysis never re-collects. Re-running the
            # battery on the SAME data is how you check a code change, not a
            # market change; re-downloading silently mixes the two.
            path = os.path.join(args.cache, f"{sym}.json")
            if os.path.exists(path):
                ticks = [tuple(t) for t in json.load(open(path, encoding="utf-8"))]
                print(f"cached {len(ticks):,} ticks for {sym}", flush=True)
            else:
                print(f"collecting {args.ticks} ticks for {sym} ...", flush=True)
                ticks = await api.tick_history(sym, count=args.ticks)
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(ticks, fh)
            epochs = [e for e, _ in ticks][1:]   # aligned to returns
            prices = [p for _, p in ticks]
            rets = returns(prices)
            dirs = [1 if r > 0 else (-1 if r < 0 else 0) for r in rets]
            series[sym] = rets
            span_h = (ticks[-1][0] - ticks[0][0]) / 3600 if len(ticks) > 1 else 0
            print(f"  {len(ticks):,} ticks, {len(rets):,} returns, {span_h:.1f}h", flush=True)

            entry: dict = {"ticks": len(ticks), "hours": round(span_h, 1),
                           "autocorr": [], "streaks": []}
            for lag in (1, 2, 3, 5, 10, 20):
                r = autocorrelation(rets, lag)
                r["test"] = f"{sym} autocorr lag{lag}"
                entry["autocorr"].append(r)
                all_results.append(r)
            for k in range(2, 11):
                s = streak_continuation(dirs, k)
                s["test"] = f"{sym} streak{k}"
                entry["streaks"].append(s)
                all_results.append(s)
            lb = ljung_box(rets, 20); lb["test"] = f"{sym} ljung-box"
            rt = runs_test(dirs); rt["test"] = f"{sym} runs"
            vc = volatility_clustering(rets, 1); vc["test"] = f"{sym} vol-cluster"
            hd = hourly_direction(epochs, dirs); hd["test"] = f"{sym} hour-direction"
            hv = hourly_volatility(epochs, rets); hv["test"] = f"{sym} hour-volatility"
            entry.update(ljung_box=lb, runs=rt, vol_cluster=vc,
                         hour_direction=hd, hour_volatility=hv)
            all_results += [lb, rt, vc, hd, hv]
            report[sym] = entry

        print("\ncross-index correlation ...", flush=True)
        cross = []
        for i, a in enumerate(symbols):
            for b in symbols[i + 1:]:
                for lag in (0, 1, 2):
                    n = min(len(series[a]), len(series[b]))
                    c = cross_correlation(series[a][:n], series[b][:n], lag)
                    c["test"] = f"{a} vs {b} lag{lag}"
                    cross.append(c)
                    all_results.append(c)
        report["cross_index"] = cross
    finally:
        await api.close()

    summary = summarise(all_results, alpha=ALPHA)
    report["summary"] = summary
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print("\n" + "=" * 72)
    print(f"{summary['tests']} tests run at alpha={ALPHA}")
    print(f"Bonferroni threshold: p < {summary['bonferroni']:.3e}")
    print(f"hits at p<{ALPHA}: {summary['hits']}   "
          f"expected by chance: {summary['expected_by_chance']:.1f}")
    print(f"VERDICT: {summary['verdict']}")
    print("=" * 72)
    if summary["survivors"]:
        print("\nSURVIVORS (these are the only ones that count):")
        for s in summary["survivors"]:
            print(f"  {s.get('test')}: p={s['p']:.3e}  "
                  f"{'r=%.4f' % s['r'] if 'r' in s else ''}"
                  f"{'p_cont=%.4f' % s['p_continue'] if 'p_continue' in s else ''}")
    print(f"\nfull report written to {args.out}")


asyncio.run(main())
