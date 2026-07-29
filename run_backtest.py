"""Backtest the pricebot strategies on real candles, against `never`."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import yaml
from dotenv import load_dotenv

from deriv_bot.api import DerivAPI
from pricebot.backtest import compare
from pricebot.signals import build_strategy


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.pricebot.yaml")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--candles", type=int, default=5000)
    ap.add_argument("--granularity", type=int, default=60)
    args = ap.parse_args()

    load_dotenv()
    token = os.environ.get("DERIV_API_TOKEN")
    if not token:
        sys.exit("Set DERIV_API_TOKEN in .env first.")
    with open(args.config, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    pb = cfg["pricebot"]
    symbols = args.symbols or pb["symbols"]

    api = DerivAPI(cfg["app_id"])
    acct = next(a for a in await api.list_accounts(token)
                if a.get("account_type") == "demo")
    await api.connect(await api.request_trading_ws_url(token, acct["account_id"]))
    try:
        for sym in symbols:
            candles = await api.candles(sym, granularity=args.granularity,
                                        count=args.candles)
            av = (await api.contracts_for(sym))["contracts_for"]["available"]
            rng = next((c.get("multiplier_range") for c in av
                        if c.get("contract_type") == "MULTUP"), [400])
            mult = sorted(rng)[0]
            p = (await api.proposal(contract_type="MULTUP", underlying_symbol=sym,
                                    amount=pb["stake"], basis="stake",
                                    currency="USD", multiplier=mult))["proposal"]
            comm = float(p.get("commission") or 0)

            strategies = {
                "never": build_strategy("never"),
                "momentum": build_strategy("momentum", lookback=20,
                                           min_move_pct=0.0004,
                                           horizon_seconds=600),
                "mean_reversion": build_strategy("mean_reversion", lookback=20,
                                                 min_move_pct=0.0004,
                                                 horizon_seconds=600),
            }
            res = compare(candles, strategies, stake=pb["stake"],
                          multiplier=mult, commission=comm,
                          granularity=args.granularity,
                          target_hold_seconds=pb["target_hold_seconds"],
                          vol_window=500)
            hours = len(candles) * args.granularity / 3600
            print(f"\n=== {sym}  x{mult}  commission {comm:.2f}  "
                  f"{len(candles)} candles ({hours:.0f}h) ===")
            print(f"{'strategy':<16}{'trades':>8}{'win%':>8}{'gross':>10}"
                  f"{'fees':>9}{'NET':>10}{'vs never':>10}")
            base = res["never"].net
            for name, r in res.items():
                print(f"{name:<16}{r.count:>8}{r.win_rate*100:>7.1f}%"
                      f"{r.gross:>10.2f}{r.commission:>9.2f}{r.net:>10.2f}"
                      f"{r.net-base:>10.2f}")
    finally:
        await api.close()


asyncio.run(main())
