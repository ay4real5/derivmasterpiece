"""Run one pricebot session. Demo only unless explicitly told otherwise."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import yaml
from dotenv import load_dotenv

from deriv_bot.api import DerivAPI
from deriv_bot.journal import TradeJournal
from pricebot.runner import Session


async def main() -> None:
    ap = argparse.ArgumentParser(description="Run a pricebot multiplier session")
    ap.add_argument("--config", default="config.pricebot.yaml")
    ap.add_argument("--minutes", type=float, default=15.0)
    args = ap.parse_args()

    load_dotenv()
    token = os.environ.get("DERIV_API_TOKEN")
    if not token:
        sys.exit("Set DERIV_API_TOKEN in .env first.")
    demo = os.environ.get("DEMO_MODE", "true").strip().lower() != "false"
    if not demo:
        sys.exit("pricebot is demo-only for now; set DEMO_MODE=true.")

    with open(args.config, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    api = DerivAPI(cfg["app_id"])
    accounts = await api.list_accounts(token)
    acct = next(a for a in accounts if a.get("account_type") == "demo")
    await api.connect(await api.request_trading_ws_url(token, acct["account_id"]))
    journal = TradeJournal(cfg.get("journal_path", "pricebot_journal.csv"))
    try:
        bal = (await api.balance())["balance"]["balance"]
        print(f"account {acct['account_id']} (DEMO) balance {float(bal):.2f}", flush=True)
        await Session(api, cfg, journal).run(args.minutes * 60)
        bal2 = (await api.balance())["balance"]["balance"]
        print(f"balance now {float(bal2):.2f} (change {float(bal2)-float(bal):+.2f})", flush=True)
    finally:
        journal.close()
        await api.close()


asyncio.run(main())
