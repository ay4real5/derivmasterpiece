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
from pricebot.instruments import CONTRACT_TYPES_FOR_INSTRUMENT
from pricebot.reconcile import reconcile_journal
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
    journal_path = cfg.get("journal_path", "pricebot_journal.csv")
    journal = TradeJournal(journal_path)
    try:
        bal = (await api.balance())["balance"]["balance"]
        print(f"account {acct['account_id']} (DEMO) balance {float(bal):.2f}", flush=True)

        # Deriv's own record is authoritative and machine-independent - this
        # is what catches a trade any process (this one on a prior run, or a
        # different machine on the same account) opened but never journaled.
        # See pricebot/reconcile.py for why this is not optional. Scoped to
        # THIS bot's own contract types: profit_table is account-wide, and
        # this account has run more than one bot on it at once.
        instrument = cfg.get("pricebot", {}).get("instrument", "multiplier")
        backfilled = await reconcile_journal(
            api, journal, journal_path,
            contract_types=CONTRACT_TYPES_FOR_INSTRUMENT.get(instrument))
        if backfilled:
            print(f"reconciled {len(backfilled)} trade(s) missing from the "
                  f"journal against Deriv's profit_table", flush=True)

        await Session(api, cfg, journal).run(args.minutes * 60)
        bal2 = (await api.balance())["balance"]["balance"]
        print(f"balance now {float(bal2):.2f} (change {float(bal2)-float(bal):+.2f})", flush=True)
    finally:
        journal.close()
        await api.close()


asyncio.run(main())
