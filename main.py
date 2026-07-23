"""CLI entrypoint: `python main.py backtest` or `python main.py live [--dry-run]`."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import yaml
from dotenv import load_dotenv

from deriv_bot.api import DerivAPI
from deriv_bot.backtester import APPROX_PAYOUT_MULTIPLIER, run_backtest
from deriv_bot.journal import TradeJournal
from deriv_bot.risk import RiskLimits, RiskManager
from deriv_bot.strategy import DigitFrequencyStrategy


def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def cmd_backtest(config: dict[str, Any]) -> None:
    report, _trades = asyncio.run(run_backtest(
        app_id=config["app_id"],
        symbol=config["symbol"],
        count=config.get("backtest_ticks", 5000),
        stake=config["stake"],
    ))
    print(
        f"Trades: {report['num_trades']}  Win rate: {report['win_rate']:.1%}  "
        f"Approx PnL: {report['total_pnl']:.2f} "
        f"(flat {APPROX_PAYOUT_MULTIPLIER}x payout assumption — see README, "
        f"not the real per-trade payout)"
    )


async def _run_live(config: dict[str, Any], dry_run: bool) -> None:
    load_dotenv()
    token = os.environ.get("DERIV_API_TOKEN")
    demo_mode = os.environ.get("DEMO_MODE", "true").strip().lower() != "false"

    if not token:
        sys.exit("Set DERIV_API_TOKEN in a .env file first (copy .env.example).")

    if not demo_mode and not dry_run:
        confirm = input(
            "DEMO_MODE=false in your .env — this will place REAL-MONEY trades.\n"
            "Type exactly: yes I understand   to continue: "
        )
        if confirm.strip().lower() != "yes i understand":
            sys.exit("Aborted — DEMO_MODE left unconfirmed.")

    strategy = DigitFrequencyStrategy(**config.get("strategy", {}))
    risk = RiskManager(RiskLimits(**config["risk"]))
    journal = TradeJournal(config.get("journal_path", "trade_journal.csv"))

    api = DerivAPI(config["app_id"])
    await api.connect()
    try:
        auth = await api.authorize(token)
        info = auth["authorize"]
        account_kind = "DEMO" if info.get("is_virtual") else "REAL"
        print(f"Authorized as {info.get('loginid')} ({account_kind} account)")
        if account_kind == "REAL" and demo_mode:
            sys.exit(
                "This API token belongs to a REAL-money account but DEMO_MODE=true. "
                "Refusing to continue — use a demo account token or explicitly set "
                "DEMO_MODE=false once you mean it."
            )

        symbol = config["symbol"]
        stake = config["stake"]
        currency = config.get("currency", "USD")

        async for tick_msg in api.subscribe({"ticks": symbol}):
            if not risk.can_trade():
                print(f"Risk manager stopped the bot: {risk.stop_reason}")
                break

            quote = tick_msg["tick"]["quote"]
            digit = int(str(quote)[-1])
            signal = strategy.on_tick(digit)
            if signal is None:
                continue

            proposal = await api.proposal(
                contract_type=signal.contract_type,
                symbol=symbol,
                amount=stake,
                basis="stake",
                duration=1,
                duration_unit="t",
                currency=currency,
                barrier=signal.barrier,
            )
            details = proposal["proposal"]
            payout = float(details["payout"])
            ask_price = float(details["ask_price"])

            if dry_run:
                print(
                    f"[dry-run] would buy {signal.contract_type} barrier={signal.barrier} "
                    f"stake={ask_price:.2f} payout={payout:.2f} — {signal.reason}"
                )
                continue

            bought = await api.buy(details["id"], ask_price)
            contract_id = bought["buy"]["contract_id"]
            print(
                f"Bought {signal.contract_type} barrier={signal.barrier} "
                f"stake={ask_price:.2f} payout={payout:.2f} contract_id={contract_id}"
            )
            # Settlement (win/loss) requires polling `proposal_open_contract`
            # until is_sold — deliberately left out until entries have been
            # validated in --dry-run / demo first. profit/balance are logged
            # as unknown here rather than guessed.
            journal.record(
                symbol=symbol, contract_type=signal.contract_type,
                barrier=signal.barrier, stake=ask_price, payout=payout,
                profit="", balance_after="", reason=signal.reason,
            )
    finally:
        journal.close()
        await api.close()


def cmd_live(config: dict[str, Any], dry_run: bool) -> None:
    asyncio.run(_run_live(config, dry_run))


def main() -> None:
    parser = argparse.ArgumentParser(description="Deriv Digits trading bot")
    sub = parser.add_subparsers(dest="mode", required=True)

    bt = sub.add_parser("backtest", help="Backtest the strategy against historical ticks")
    bt.add_argument("--config", default="config.yaml")

    live = sub.add_parser("live", help="Run against live ticks (demo account by default)")
    live.add_argument("--config", default="config.yaml")
    live.add_argument("--dry-run", action="store_true", help="Compute signals but never place trades")

    args = parser.parse_args()
    config = load_config(args.config)

    if args.mode == "backtest":
        cmd_backtest(config)
    else:
        cmd_live(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
