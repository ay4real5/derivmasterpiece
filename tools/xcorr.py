"""Are Deriv's synthetic feeds actually independent of one another?

Every statistical test in this repo so far has examined ONE symbol at a time
(see TICK_ANALYSIS.md - 260 tests on 864,000 ticks, zero survivors). Nobody has
asked whether two DIFFERENT synthetics are correlated with each other.

Why it is worth asking: Deriv generates 46 synthetic feeds from some shared
random-number infrastructure. If any two share entropy - a seed family,
correlated streams, a common driving process, an implementation artefact - then
one symbol's tick carries information about another's NEXT tick. That is
prediction, not staking, so unlike a ladder it would genuinely survive the
2-4% house margin.

    python -m tools.xcorr collect --minutes 240     # accumulate ticks
    python -m tools.xcorr test                      # run the analysis

WHY A COLLECTOR AND NOT ONE FETCH. `ticks_history` caps at 1000 ticks per
symbol. At n=999 the standard error on a correlation is 0.032, so a Bonferroni-
corrected test can only see |r| > 0.089. But the correlation needed to actually
PROFIT is smaller than that:

    P(correct direction) = 0.5 + arcsin(r)/pi
    break-even at Deriv's 1.9233x payout = 51.99%
    => r > 0.063 is already tradeable

So a single fetch leaves a blind spot between r=0.063 (profitable) and r=0.089
(detectable) - an edge could exist there and the test would miss it. At
n=5,000 the detection threshold falls to r=0.040, safely below the profit line.
Hence: accumulate first, then test.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from dotenv import load_dotenv                      # noqa: E402
from deriv_bot.api import DerivAPI                  # noqa: E402

STORE = os.path.join(REPO, "xcorr_ticks.json")
SYMBOLS = ["R_10", "R_25", "R_50", "R_75", "R_100",
           "1HZ10V", "1HZ50V", "1HZ100V"]

# Deriv's Rise/Fall payout; break-even is stake/payout.
PAYOUT = 1.9233
BREAK_EVEN = 1.0 / PAYOUT


def profitable_r() -> float:
    """Smallest |r| whose directional accuracy clears the break-even rate."""
    return math.sin((BREAK_EVEN - 0.5) * math.pi)


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}Z] {msg}", flush=True)


def load_store() -> dict:
    if not os.path.exists(STORE):
        return {}
    try:
        with open(STORE, encoding="utf-8") as fh:
            return {k: {int(t): float(p) for t, p in v.items()}
                    for k, v in json.load(fh).items()}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def save_store(store: dict) -> None:
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({k: {str(t): p for t, p in v.items()}
                   for k, v in store.items()}, fh)
    os.replace(tmp, STORE)          # atomic: never leave a half-written store


async def collect(minutes: float, every: float) -> None:
    import yaml
    load_dotenv(os.path.join(REPO, ".env"), override=True)
    token = os.environ.get("DERIV_API_TOKEN")
    if not token:
        sys.exit("Set DERIV_API_TOKEN in .env first.")
    with open(os.path.join(REPO, "config.yaml"), encoding="utf-8") as fh:
        app_id = yaml.safe_load(fh)["app_id"]

    store = load_store()
    deadline = time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        api = DerivAPI(app_id)
        try:
            acct = next(a for a in await api.list_accounts(token)
                        if a.get("account_type") == "demo")
            url = await api.request_trading_ws_url(token, acct["account_id"])
            await api.connect(url)
            added = 0
            for sym in SYMBOLS:
                try:
                    h = (await api.ticks_history(sym, count=1000))["history"]
                except Exception as exc:                      # noqa: BLE001
                    log(f"  {sym}: {type(exc).__name__}")
                    continue
                bucket = store.setdefault(sym, {})
                before = len(bucket)
                for t, p in zip(h["times"], h["prices"]):
                    bucket[int(t)] = float(p)
                added += len(bucket) - before
            save_store(store)
            log(f"+{added} new | " +
                " ".join(f"{s}:{len(store.get(s, {}))}" for s in SYMBOLS))
        except Exception as exc:                              # noqa: BLE001
            log(f"poll failed ({type(exc).__name__}) - retrying next cycle")
        finally:
            try:
                await api.close()
            except Exception:                                 # noqa: BLE001
                pass
        await asyncio.sleep(every)


def corr(x: list, y: list) -> float:
    n = len(x)
    if n < 3:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((v - mx) ** 2 for v in x))
    sy = math.sqrt(sum((v - my) ** 2 for v in y))
    if sx == 0 or sy == 0:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def returns(series: dict, times: list) -> list:
    return [math.log(series[b] / series[a]) for a, b in zip(times, times[1:])]


def bonferroni_z(ntests: int, alpha: float = 0.05) -> float:
    """Two-sided Bonferroni critical |z|, by bisection on the normal CDF.

    Computed rather than hardcoded so the threshold tracks however many pairs
    actually had enough overlapping data on the day.
    """
    target = 1 - alpha / (2 * ntests)
    lo, hi = 0.0, 8.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if 0.5 * (1 + math.erf(mid / math.sqrt(2))) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def run_test() -> int:
    store = load_store()
    if not store:
        sys.exit(f"No data in {STORE}. Run: python -m tools.xcorr collect")
    syms = sorted(store)
    rows = []
    for a, b in itertools.combinations(syms, 2):
        common = sorted(set(store[a]) & set(store[b]))
        if len(common) < 200:
            continue
        ra, rb = returns(store[a], common), returns(store[b], common)
        n = len(ra)
        rows.append((a, b, n,
                     corr(ra, rb),            # same instant
                     corr(ra[:-1], rb[1:]),   # A leads B
                     corr(ra[1:], rb[:-1]),   # B leads A
                     1 / math.sqrt(n)))
    if not rows:
        sys.exit("Not enough overlapping ticks yet - collect for longer.")

    ntests = len(rows) * 3
    crit = bonferroni_z(ntests)
    nmin = min(r[2] for r in rows)
    detectable = crit / math.sqrt(nmin)
    need = profitable_r()

    print("CROSS-SYMBOL INDEPENDENCE TEST\n")
    print(f"  pairs {len(rows)} x 3 lags = {ntests} tests")
    print(f"  Bonferroni |z| threshold      {crit:.3f}")
    print(f"  smallest n                    {nmin}")
    print(f"  smallest DETECTABLE |r|       {detectable:.4f}")
    print(f"  smallest PROFITABLE |r|       {need:.4f}  "
          f"(clears {BREAK_EVEN * 100:.2f}% break-even)")
    if detectable > need:
        print(f"  ** BLIND SPOT {need:.4f}-{detectable:.4f} "
              f"- collect more ticks **\n")
    else:
        print("  no blind spot: this test can see every profitable "
              "correlation\n")

    print(f"{'pair':<24}{'n':>7}{'same':>9}{'z':>7}"
          f"{'A->B':>9}{'z':>7}{'B->A':>9}{'z':>7}")
    print("-" * 79)
    hits = []
    ordered = sorted(rows, key=lambda r: -max(abs(r[3]), abs(r[4]), abs(r[5])))
    for a, b, n, c0, c1, cm1, se in ordered:
        z0, z1, zm1 = c0 / se, c1 / se, cm1 / se
        for name, c, z in (("same", c0, z0), ("A->B", c1, z1),
                           ("B->A", cm1, zm1)):
            if abs(z) > crit:
                hits.append((a, b, name, c, z))
        print(f"{a + '/' + b:<24}{n:>7}{c0:>9.4f}{z0:>7.2f}"
              f"{c1:>9.4f}{z1:>7.2f}{cm1:>9.4f}{zm1:>7.2f}")

    print()
    if hits:
        print("SURVIVED CORRECTION - investigate before believing it:")
        for a, b, name, c, z in hits:
            acc = 0.5 + math.asin(max(-1.0, min(1.0, c))) / math.pi
            print(f"   {a}/{b} {name}: r={c:+.4f} z={z:+.2f} "
                  f"-> {acc * 100:.2f}% directional vs "
                  f"{BREAK_EVEN * 100:.2f}% needed")
        return 1
    print("Nothing survived correction - consistent with independent feeds.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="accumulate ticks into xcorr_ticks.json")
    c.add_argument("--minutes", type=float, default=240.0)
    c.add_argument("--every", type=float, default=300.0)
    sub.add_parser("test", help="run the correlation analysis")
    args = ap.parse_args()
    if args.cmd == "collect":
        asyncio.run(collect(args.minutes, args.every))
        return 0
    return run_test()


if __name__ == "__main__":
    raise SystemExit(main())
