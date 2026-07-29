"""Scan every open market for volatility predictability AND an instrument
that can express it intraday."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import yaml
from dotenv import load_dotenv

from deriv_bot.api import DerivAPI
from pricebot.market_scan import (
    direction_persistence,
    intraday_vol_instruments,
    score,
    vol_persistence,
)


async def one(api, sym, market, granularity, count):
    row = {"symbol": sym, "market": market}
    try:
        candles = await api.candle_history(sym, granularity=granularity,
                                           count=count, page_pause=0.05)
        row["candles"] = len(candles)
        row["vol"] = vol_persistence(candles)
        row["dir"] = direction_persistence(candles)
    except Exception as exc:
        return {**row, "error": type(exc).__name__}
    try:
        cf = await api.contracts_for(sym)
        row["inst"] = intraday_vol_instruments(cf)
    except Exception as exc:
        row["inst"] = {}
        row["error"] = f"contracts: {type(exc).__name__}"
    row["score"] = score(row.get("vol", 0.0), row.get("inst", {}))
    return row


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--granularity", type=int, default=900)
    ap.add_argument("--candles", type=int, default=4000)
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    load_dotenv()
    token = os.environ["DERIV_API_TOKEN"]
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    api = DerivAPI(cfg["app_id"])
    acct = next(a for a in await api.list_accounts(token)
                if a.get("account_type") == "demo")
    await api.connect(await api.request_trading_ws_url(token, acct["account_id"]))
    try:
        syms = [s for s in (await api.send({"active_symbols": "brief"}))["active_symbols"]
                if s.get("exchange_is_open")]
        # a spread across markets rather than 25 forex pairs
        picked, seen = [], {}
        for s in syms:
            m = s.get("market", "?")
            seen[m] = seen.get(m, 0) + 1
            if seen[m] <= 8:
                picked.append((s["underlying_symbol"], m))
        picked = picked[: args.limit]
        print(f"scanning {len(picked)} symbols at {args.granularity}s candles ...",
              flush=True)

        rows = []
        for i in range(0, len(picked), 4):          # small batches, be polite
            batch = picked[i:i + 4]
            rows += list(await asyncio.gather(
                *(one(api, s, m, args.granularity, args.candles) for s, m in batch)))
            print(f"  {len(rows)}/{len(picked)}", flush=True)

        rows.sort(key=lambda r: r.get("score", 0), reverse=True)
        print(f"\n{'symbol':<14}{'market':<16}{'vol persist':>12}{'dir':>7}"
              f"{'candles':>9}  intraday vol instruments")
        for r in rows:
            if r.get("error") and "vol" not in r:
                print(f"{r['symbol']:<14}{r['market']:<16}  {r['error']}")
                continue
            inst = ", ".join(f"{k}>={v}" for k, v in sorted(r.get("inst", {}).items())) or "NONE"
            print(f"{r['symbol']:<14}{r['market']:<16}{r['vol']:>+12.3f}"
                  f"{r['dir']:>7.1%}{r.get('candles',0):>9}  {inst}")
        print("\nWanted: high vol persistence AND an intraday instrument.")
        print("dir should sit near 50% everywhere; a big deviation means a bad feed,")
        print("not an edge.")
    finally:
        await api.close()


asyncio.run(main())
