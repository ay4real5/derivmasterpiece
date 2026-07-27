"""CLI entrypoint: `python main.py backtest [--compare]`, `python main.py
scan-edge`, or `python main.py live [--dry-run]`."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Any

import yaml
from dotenv import load_dotenv

from deriv_bot.analysis import analyze_journal
from deriv_bot.api import DerivAPI
from deriv_bot.backtester import (
    approx_net_win, backtest_over_prices, fetch_ticks, last_digit, run_backtest,
)
from deriv_bot.edge import scan_edge
from deriv_bot.journal import TradeJournal
from deriv_bot.multi_scan import (
    CATEGORY_LEGS, DEFAULT_CANDIDATES, DEFAULT_SYMBOLS, RoundRobin, parse_candidate_specs, scan_best,
)
from deriv_bot.risk import RiskLimits, RiskManager
from deriv_bot.staking import build_staker
from deriv_bot.strategy import STRATEGIES, Strategy, build_strategy

MIN_STAKE = 0.35  # Deriv's minimum contract stake

# Every combination in a scan failing means the connection is gone, not that
# the market is quiet — a live scan normally returns ~60 quotes. Retrying
# forever on the same dead websocket just spins: observed as an 18-minute
# stall logging "scan returned no quotes" while placing no trades. Give it a
# few cycles to cover a transient blip, then exit non-zero so the supervisor
# restarts with a fresh connection.
MAX_EMPTY_SCANS = 3


def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_strategy_from_config(config: dict[str, Any]) -> Strategy:
    strategy_cfg = dict(config.get("strategy", {}))
    name = strategy_cfg.pop("name", "digit_frequency")
    return build_strategy(name, **strategy_cfg)


def cmd_backtest(config: dict[str, Any], compare: bool = False) -> None:
    if compare:
        prices, pip_size = asyncio.run(fetch_ticks(
            config["app_id"], config["symbol"], config.get("backtest_ticks", 5000),
        ))
        rows = []
        for name, cls in STRATEGIES.items():
            report, _trades = backtest_over_prices(prices, config["stake"], cls(), pip_size)
            rows.append((name, report))
        rows.sort(key=lambda r: r[1]["total_pnl"], reverse=True)

        print(f"{'strategy':<20}{'trades':>8}{'win rate':>10}{'approx pnl':>14}")
        for name, report in rows:
            print(f"{name:<20}{report['num_trades']:>8}{report['win_rate']:>9.1%}{report['total_pnl']:>14.2f}")
        print(
            "\n(approximate per-contract payout table, default params for "
            "every strategy — see deriv_bot/backtester.py, not the real "
            "per-trade payout. This ranks ideas against the same data; it does "
            "not prove any of them hold up out-of-sample.)"
        )
        return

    strategy = _build_strategy_from_config(config)
    report, _trades = asyncio.run(run_backtest(
        app_id=config["app_id"],
        symbol=config["symbol"],
        count=config.get("backtest_ticks", 5000),
        stake=config["stake"],
        strategy=strategy,
    ))
    print(
        f"Trades: {report['num_trades']}  Win rate: {report['win_rate']:.1%}  "
        f"Approx PnL: {report['total_pnl']:.2f} "
        f"(approximate per-contract payout table — see deriv_bot/backtester.py, "
        f"not the real per-trade payout)"
    )


def cmd_scan_edge(config: dict[str, Any]) -> None:
    load_dotenv()
    token = os.environ.get("DERIV_API_TOKEN")
    if not token:
        sys.exit(
            "Set DERIV_API_TOKEN in a .env file first (copy .env.example) — "
            "Deriv's proposal/payout data requires an authorized session, "
            "even just to look up prices."
        )

    results = asyncio.run(scan_edge(
        config["app_id"], config["symbol"], config["stake"], config.get("currency", "USD"), token,
    ))
    if not results:
        sys.exit(
            "No contracts returned a quote — check that the symbol in config.yaml "
            "is currently tradeable and that your token/account supports it."
        )
    print(f"{'contract':<12}{'barrier':>8}{'dur':>5}{'win prob':>10}{'payout':>10}{'ask':>8}{'EV':>9}{'edge %':>9}")
    for r in results:
        barrier = r["barrier"] if r["barrier"] is not None else "-"
        dur = f"{r.get('duration', 1)}t"
        print(
            f"{r['contract_type']:<12}{barrier:>8}{dur:>5}{r['win_prob']:>10.1%}"
            f"{r['payout']:>10.2f}{r['ask_price']:>8.2f}{r['ev']:>9.3f}{r['edge_pct']:>8.2f}%"
        )
    print(
        "\nLowest edge % = smallest house margin on this contract right now. "
        "win prob is the theoretical value (digits are ~uniform), not a prediction — "
        "this tells you which bet is cheapest, not which one will win."
    )


def cmd_analyze(config: dict[str, Any]) -> None:
    path = config.get("journal_path", "trade_journal.csv")
    try:
        result = analyze_journal(path)
    except FileNotFoundError:
        sys.exit(f"No journal found at {path} — run `live` (demo) first to generate trades.")

    overall = result["overall"]
    if overall["trades"] == 0:
        sys.exit(f"{path} has no settled trades yet (dry-run rows don't count).")

    print(f"{'contract':<12}{'barrier':>8}{'trades':>8}{'win rate':>10}{'total pnl':>11}{'avg/trade':>11}{'loss % of staked':>18}")
    for (contract, barrier), s in result["by_contract"].items():
        print(
            f"{contract:<12}{barrier:>8}{s['trades']:>8}{s['win_rate']:>9.1%}"
            f"{s['total_pnl']:>11.2f}{s['avg_pnl']:>11.3f}{s['loss_pct_of_staked']:>17.2f}%"
        )
    print(
        f"\nOverall: {overall['trades']} trades, {overall['win_rate']:.1%} win rate, "
        f"{overall['total_pnl']:+.2f} PnL ({overall['loss_pct_of_staked']:.2f}% of "
        f"{overall['total_staked']:.2f} staked lost)"
    )
    print(
        "Honest benchmark: expect win rate ≈ each contract's theoretical probability "
        "and loss ≈ its house margin (see scan-edge). Deviations on small samples "
        "are noise, not signal."
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

    strategy = _build_strategy_from_config(config)
    risk = RiskManager(RiskLimits(**config["risk"]))
    journal = TradeJournal(config.get("journal_path", "trade_journal.csv"))

    staking_cfg = dict(config.get("staking", {}))
    staking_name = staking_cfg.pop("name", "flat")
    if staking_name != "flat" and not demo_mode:
        sys.exit(
            f"staking '{staking_name}' is DEMO ONLY and DEMO_MODE is false. "
            "Progressive staking on a real-money account is refused by design — "
            "see deriv_bot/staking.py and tools/martingale_sim.py."
        )
    staker = build_staker(staking_name, **staking_cfg)

    api = DerivAPI(config["app_id"])
    accounts = await api.list_accounts(token)
    wanted_type = "real" if not demo_mode else "demo"
    account = next((a for a in accounts if a["account_type"] == wanted_type), None)
    if account is None:
        sys.exit(
            f"No {wanted_type} account found for this token/app — check "
            f"DEMO_MODE and that the token has access to a {wanted_type} account."
        )

    ws_url = await api.request_trading_ws_url(token, account["account_id"])
    await api.connect(ws_url)
    try:
        print(f"Authorized as {account['account_id']} ({wanted_type.upper()} account)")

        symbol = config["symbol"]
        base_stake = config["stake"]
        currency = account.get("currency", config.get("currency", "USD"))

        async for tick_msg in api.subscribe({"ticks": symbol}):
            if not risk.can_trade():
                print(f"Risk manager stopped the bot: {risk.stop_reason}")
                break

            tick = tick_msg["tick"]
            digit = last_digit(tick["quote"], int(tick["pip_size"]))
            signal = strategy.on_tick(digit)
            if signal is None:
                continue

            # A staker may swap the contract (e.g. smart_recovery routes
            # recovery bets to whichever contract is cheapest to recover on).
            override = staker.override_signal(signal)
            if override is not None:
                print(override.reason)
                signal = override

            # Size this bet. `net_mult` is what a win pays per 1.0 staked —
            # progressive staking needs it to know how much recovers a run.
            net_mult = approx_net_win(signal, 1.0)
            budget_left = abs(risk.limits.max_daily_loss) + risk.daily_pnl
            stake = round(staker.stake_for(base_stake, net_mult, budget_left), 2)
            if stake < MIN_STAKE:
                print(f"Remaining budget ${budget_left:.2f} below minimum stake — stopping.")
                break

            proposal_params: dict[str, Any] = dict(
                contract_type=signal.contract_type,
                underlying_symbol=symbol,
                amount=stake,
                basis="stake",
                duration=1,
                duration_unit="t",
                currency=currency,
            )
            if signal.barrier is not None:
                proposal_params["barrier"] = signal.barrier
            proposal = await api.proposal(**proposal_params)
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

            # Digit contracts settle in one tick, so waiting here before
            # picking up the next signal keeps risk accounting strictly
            # ordered rather than juggling concurrent settlements.
            contract = await api.wait_for_settlement(contract_id)
            profit = float(contract["profit"])
            bal = await api.balance()
            balance_after = bal["balance"]["balance"]
            print(f"Settled contract_id={contract_id} profit={profit:.2f} balance={balance_after:.2f}")

            risk.record_trade(profit)
            staker.record(profit)
            journal.record(
                symbol=symbol, contract_type=signal.contract_type,
                barrier=signal.barrier, stake=ask_price, payout=payout,
                profit=profit, balance_after=balance_after, reason=signal.reason,
            )
    finally:
        journal.close()
        await api.close()


def cmd_live(config: dict[str, Any], dry_run: bool) -> None:
    asyncio.run(_run_live(config, dry_run))


async def _run_scan_trade(config: dict[str, Any], dry_run: bool) -> None:
    """Every cycle: quote every configured symbol x contract type, trade
    whichever quote has the smallest house margin right now, then wait out
    the rest of `interval_seconds` before scanning again. No tick stream is
    watched — digit contracts settle on their own, and there is nothing in
    a digit's history worth reacting to (see README). The only genuine
    lever is picking the cheapest bet on offer, and that varies by symbol.
    """
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

    st_cfg = config.get("scan_trade", {})
    symbols = st_cfg.get("symbols", DEFAULT_SYMBOLS)
    interval = float(st_cfg.get("interval_seconds", 45))
    candidates = (
        parse_candidate_specs(st_cfg["contracts"]) if st_cfg.get("contracts") else DEFAULT_CANDIDATES
    )
    # Force interchange: a pure "pick the single cheapest quote" greedily
    # starves whichever categories/symbols are never quite the cheapest —
    # observed live picking the same symbol+contract every cycle. These two
    # independent round-robins guarantee every category and every symbol
    # gets its turn; scoring only picks the best LEG within the forced cell.
    category_rr = RoundRobin(list(CATEGORY_LEGS))
    symbol_rr = RoundRobin(symbols)

    risk = RiskManager(RiskLimits(**config["risk"]))
    journal = TradeJournal(config.get("journal_path", "trade_journal.csv"))

    staking_cfg = dict(config.get("staking", {}))
    staking_name = staking_cfg.pop("name", "flat")
    if staking_name != "flat" and not demo_mode:
        sys.exit(
            f"staking '{staking_name}' is DEMO ONLY and DEMO_MODE is false. "
            "Progressive staking on a real-money account is refused by design — "
            "see deriv_bot/staking.py and tools/martingale_sim.py."
        )
    staker = build_staker(staking_name, **staking_cfg)
    base_stake = config["stake"]

    api = DerivAPI(config["app_id"])
    accounts = await api.list_accounts(token)
    wanted_type = "real" if not demo_mode else "demo"
    account = next((a for a in accounts if a["account_type"] == wanted_type), None)
    if account is None:
        sys.exit(
            f"No {wanted_type} account found for this token/app — check "
            f"DEMO_MODE and that the token has access to a {wanted_type} account."
        )

    ws_url = await api.request_trading_ws_url(token, account["account_id"])
    await api.connect(ws_url)
    try:
        print(
            f"Authorized as {account['account_id']} ({wanted_type.upper()} account) — "
            f"scanning {len(symbols)} symbols x {len(candidates)} contracts every {interval:.0f}s"
        )
        currency = account.get("currency", config.get("currency", "USD"))

        empty_scans = 0
        while risk.can_trade():
            cycle_start = time.monotonic()
            scan_errors: list[str] = []
            results = await scan_best(api, symbols, candidates, base_stake, currency,
                                      errors=scan_errors)
            if not results:
                empty_scans += 1
                detail = f" — first error: {scan_errors[0]}" if scan_errors else ""
                print(f"scan returned no quotes ({empty_scans}/{MAX_EMPTY_SCANS}){detail}")
                if empty_scans >= MAX_EMPTY_SCANS:
                    print(
                        f"{empty_scans} consecutive empty scans — the connection looks dead. "
                        "Exiting so the supervisor reconnects."
                    )
                    raise SystemExit(3)
            else:
                empty_scans = 0
                overall_best = results[0]
                print(
                    f"scanned {len(results)} quotes across {len(symbols)} symbols — cheapest overall: "
                    f"{overall_best['symbol']} {overall_best['contract_type']} "
                    f"(edge {overall_best['edge_pct']:.2f}%) — not necessarily this cycle's pick"
                )

                category = category_rr.next()
                symbol = symbol_rr.next()
                legs = CATEGORY_LEGS[category]
                cell = [r for r in results if r["symbol"] == symbol
                       and (r["contract_type"], r["barrier"]) in legs]
                if not cell:
                    print(f"{category} not offered on {symbol} this cycle — skipping")
                    elapsed = time.monotonic() - cycle_start
                    if elapsed < interval:
                        await asyncio.sleep(interval - elapsed)
                    continue
                best = min(cell, key=lambda r: r["edge_pct"])
                barrier_desc = "" if best["barrier"] is None else f":{best['barrier']}"
                print(
                    f"this cycle's turn: {category} on {symbol} -> picked "
                    f"{best['contract_type']}{barrier_desc} (edge {best['edge_pct']:.2f}%, "
                    f"win prob {best['win_prob']:.0%})"
                )

                # Real quoted net multiplier, not the backtester's approximation —
                # we just fetched the actual payout, so use it.
                net_mult = best["payout"] / best["ask_price"] - 1 if best["ask_price"] else 0.0
                budget_left = abs(risk.limits.max_daily_loss) + risk.daily_pnl
                stake = round(staker.stake_for(base_stake, net_mult, budget_left), 2)
                if stake < MIN_STAKE:
                    print(f"Remaining budget ${budget_left:.2f} below minimum stake — stopping.")
                    break

                params: dict[str, Any] = dict(
                    contract_type=best["contract_type"], underlying_symbol=best["symbol"],
                    amount=stake, basis="stake", duration=1, duration_unit="t", currency=currency,
                )
                if best["barrier"] is not None:
                    params["barrier"] = best["barrier"]
                proposal = await api.proposal(**params)
                details = proposal["proposal"]
                payout = float(details["payout"])
                ask_price = float(details["ask_price"])
                reason = f"rotation: {category} on {symbol} ({best['edge_pct']:.2f}% edge)"

                if dry_run:
                    print(
                        f"[dry-run] would buy {best['symbol']} {best['contract_type']}{barrier_desc} "
                        f"stake={ask_price:.2f} payout={payout:.2f} — {reason}"
                    )
                else:
                    bought = await api.buy(details["id"], ask_price)
                    contract_id = bought["buy"]["contract_id"]
                    print(
                        f"Bought {best['symbol']} {best['contract_type']}{barrier_desc} "
                        f"stake={ask_price:.2f} payout={payout:.2f} contract_id={contract_id}"
                    )
                    contract = await api.wait_for_settlement(contract_id)
                    profit = float(contract["profit"])
                    bal = await api.balance()
                    balance_after = bal["balance"]["balance"]
                    print(f"Settled contract_id={contract_id} profit={profit:.2f} balance={balance_after:.2f}")

                    risk.record_trade(profit)
                    staker.record(profit)
                    journal.record(
                        symbol=best["symbol"], contract_type=best["contract_type"],
                        barrier=best["barrier"], stake=ask_price, payout=payout,
                        profit=profit, balance_after=balance_after, reason=reason,
                    )
                    if not risk.can_trade():
                        print(f"Risk manager stopped the bot: {risk.stop_reason}")
                        break

            elapsed = time.monotonic() - cycle_start
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)
    finally:
        journal.close()
        await api.close()


def cmd_scan_trade(config: dict[str, Any], dry_run: bool) -> None:
    asyncio.run(_run_scan_trade(config, dry_run))


def main() -> None:
    parser = argparse.ArgumentParser(description="Deriv Digits trading bot")
    sub = parser.add_subparsers(dest="mode", required=True)

    bt = sub.add_parser("backtest", help="Backtest the strategy against historical ticks")
    bt.add_argument("--config", default="config.yaml")
    bt.add_argument(
        "--compare", action="store_true",
        help="Backtest every registered strategy (default params) and rank them",
    )

    scan = sub.add_parser(
        "scan-edge",
        help="Query live payouts across Digits contracts/barriers and rank by smallest house edge",
    )
    scan.add_argument("--config", default="config.yaml")

    live = sub.add_parser("live", help="Run against live ticks (demo account by default)")
    live.add_argument("--config", default="config.yaml")
    live.add_argument("--dry-run", action="store_true", help="Compute signals but never place trades")

    st = sub.add_parser(
        "scan-trade",
        help="Every cycle, quote every symbol x contract and trade whichever is cheapest right now",
    )
    st.add_argument("--config", default="config.yaml")
    st.add_argument("--dry-run", action="store_true", help="Compute the pick but never place trades")

    an = sub.add_parser("analyze", help="Report per-contract performance from the trade journal")
    an.add_argument("--config", default="config.yaml")

    args = parser.parse_args()
    config = load_config(args.config)

    if args.mode == "backtest":
        cmd_backtest(config, compare=args.compare)
    elif args.mode == "scan-edge":
        cmd_scan_edge(config)
    elif args.mode == "analyze":
        cmd_analyze(config)
    elif args.mode == "scan-trade":
        cmd_scan_trade(config, dry_run=args.dry_run)
    else:
        cmd_live(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
