"""Trade only at the levels in lines.json. Demo only unless told otherwise.

    python run_sr_bot.py --minutes 60
    python run_sr_bot.py --dry-run          # decide and log, never buy

Reloads lines.json every cycle, so you can add or move a level while it runs
without restarting and without resetting any line's cooldown.

Every cycle writes a line to signals.csv - taken or skipped, with the reason.
That file is the point of the exercise: after 50-100 decisions it tells you
whether your levels are wrong, or the confirmation is simply too strict, or the
bot never got near a line at all. A P&L curve alone cannot separate those three.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import time
from datetime import datetime, timezone

import yaml
from dotenv import load_dotenv

from deriv_bot.api import DerivAPI
from pricebot.sr_lines import (
    Limits, break_even, decide, load_lines, mark_broken, merge_state,
    trend_direction,
)
from tools import lockfile

SIGNALS_CSV = "signals.csv"
TRADES_CSV = "sr_trades.csv"
LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sr_bot.lock")

# Consecutive data failures before tearing the socket down and redialling, and
# how many redials before admitting the problem is not the market.
MAX_FAILS = 3
MAX_RECONNECTS = 10


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def log(msg: str) -> None:
    print(f"[{now_utc():%H:%M:%S}Z] {msg}", flush=True)


def append(path: str, row: dict) -> None:
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        if not exists:
            w.writeheader()
        w.writerow(row)


def day_pnl(path: str, day: str) -> float:
    """Today's realised PnL, rounded to cents.

    Rounded because an exact comparison against an accumulated float is what
    stopped the digit bot's daily cap firing at -899.9999999999989 against 900.
    """
    if not os.path.exists(path):
        return 0.0
    total = 0.0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("timestamp") or "").startswith(day):
                continue
            raw = (row.get("profit") or "").strip()
            if not raw:
                continue
            try:
                total += float(raw)
            except ValueError:
                continue
    return round(total, 2)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lines", default="lines.json")
    ap.add_argument("--symbol", default="R_50")
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--poll", type=float, default=15.0)
    ap.add_argument("--duration", type=int, default=55)
    ap.add_argument("--stake", type=float, default=5.0)
    ap.add_argument("--max-daily-loss", type=float, default=1000.0)
    ap.add_argument("--cooldown", type=int, default=1800)
    ap.add_argument("--max-per-line", type=int, default=3)
    ap.add_argument("--martingale-steps", type=int, default=6,
                    help="max consecutive losing trades before resetting "
                         "stake to base (0 = flat, no ladder)")
    ap.add_argument("--martingale-mult", type=float, default=2.0,
                    help="stake multiplier after each loss (2.0 = classic "
                         "double-up)")
    ap.add_argument("--dry-run", action="store_true",
                    help="decide and log, never place an order")
    args = ap.parse_args()

    load_dotenv(override=True)
    token = os.environ.get("DERIV_API_TOKEN")
    if not token:
        sys.exit("Set DERIV_API_TOKEN in .env first.")
    if os.environ.get("DEMO_MODE", "true").strip().lower() == "false":
        sys.exit("This bot is demo-only. Unset DEMO_MODE=false to run it.")

    if not lockfile.acquire(LOCK_PATH):
        sys.exit("another run_sr_bot.py already holds the lock - exiting "
                 "rather than doubling the trade rate against the same "
                 "lines.json and the same daily-loss cap")

    try:
        await _run(args, token)
    finally:
        lockfile.release(LOCK_PATH)


async def _run(args, token: str) -> None:
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    limits = Limits(stake=args.stake, max_daily_loss=args.max_daily_loss,
                    cooldown_seconds=args.cooldown,
                    max_trades_per_line_per_day=args.max_per_line)

    try:
        lines = load_lines(args.lines)
    except (ValueError, FileNotFoundError) as exc:
        sys.exit(f"lines.json: {exc}")
    live = [ln for ln in lines if ln.active]
    if not live:
        sys.exit(f"No active lines in {args.lines}. Add your levels and set "
                 f"active:true - the bot has nothing to watch otherwise.")

    api = DerivAPI(cfg["app_id"])
    acct = next(a for a in await api.list_accounts(token)
                if a.get("account_type") == "demo")
    await api.connect(await api.request_trading_ws_url(token, acct["account_id"]))

    try:
        bal = float((await api.balance())["balance"]["balance"])
        log(f"{acct['account_id']} (DEMO) balance {bal:.2f}")
        log(f"watching {len(live)} line(s) on {args.symbol}, "
            f"{args.duration}s Rise/Fall, stake {args.stake:.2f}"
            + ("  [DRY RUN]" if args.dry_run else ""))
        if args.martingale_steps > 0:
            ladder = [args.stake * args.martingale_mult ** i
                      for i in range(args.martingale_steps)]
            log(f"   martingale: {args.martingale_steps} steps x{args.martingale_mult} "
                f"-> {', '.join(f'{s:.2f}' for s in ladder)} "
                f"(max ladder loss ${sum(ladder):.2f})")
        for ln in live:
            log(f"   {ln.name}: {ln.type} @ {ln.price_level:.4f} "
                f"+/-{ln.tolerance_pct:.3f}% ({ln.timeframe})")

        deadline = time.monotonic() + args.minutes * 60
        open_trades = 0
        taken = 0
        fails = 0
        reconnect_fails = 0
        ladder_step = 0
        current_stake = args.stake

        while time.monotonic() < deadline:
            cycle = time.monotonic()
            day = now_utc().strftime("%Y-%m-%d")

            # Reload so edits land without a restart, keeping cooldowns.
            try:
                lines = merge_state(lines, load_lines(args.lines))
            except (ValueError, FileNotFoundError) as exc:
                log(f"lines.json unreadable, keeping the previous set: {exc}")
            active = [ln for ln in lines if ln.active]
            for ln in active:
                if ln.first_seen_epoch is None:
                    ln.first_seen_epoch = int(time.time())

            try:
                candles = await api.candles(args.symbol, granularity=60, count=10)
                candles_15m = await api.candles(args.symbol, granularity=900, count=20)
                price = float((await api.ticks_history(args.symbol, count=1))
                              ["history"]["prices"][-1])
                quote = await api.proposal(
                    contract_type="CALL", underlying_symbol=args.symbol,
                    amount=args.stake, basis="stake", currency="USD",
                    duration=args.duration, duration_unit="s")
                payout = float(quote["proposal"]["payout"]) / args.stake
            except Exception as exc:                      # noqa: BLE001
                # RECONNECT, do not just skip. A dropped websocket does not
                # heal by waiting: the first version logged "market data
                # unavailable" every 20 seconds for hours while the socket
                # stayed dead and no trade was ever considered. The digit bot
                # survives this only because its supervisor restarts the child;
                # a standalone script has to do it itself.
                fails += 1
                log(f"market data unavailable ({type(exc).__name__}) "
                    f"[{fails}/{MAX_FAILS}]")
                if fails >= MAX_FAILS:
                    log("reconnecting the websocket ...")
                    try:
                        await api.close()
                    except Exception:                     # noqa: BLE001
                        pass
                    try:
                        url = await api.request_trading_ws_url(
                            token, acct["account_id"])
                        await api.connect(url)
                        bal = float((await api.balance())["balance"]["balance"])
                        log(f"reconnected, balance {bal:.2f}")
                        fails = 0
                    except Exception as exc2:             # noqa: BLE001
                        reconnect_fails += 1
                        log(f"reconnect failed ({type(exc2).__name__}) - "
                            f"attempt {reconnect_fails}/{MAX_RECONNECTS}")
                        if reconnect_fails >= MAX_RECONNECTS:
                            log("giving up; the token or the network is the "
                                "problem, not the market")
                            return
                        await asyncio.sleep(min(60, 5 * reconnect_fails))
                await asyncio.sleep(args.poll)
                continue
            else:
                fails = 0
                reconnect_fails = 0

            for ln in mark_broken(active, price):
                log(f"LINE BROKEN: {ln.name} - price {price:.4f} closed through "
                    f"{ln.price_level:.4f}; it will not be traded again")

            pnl = day_pnl(TRADES_CSV, day)
            trend = trend_direction(candles_15m)
            d = decide(price, active, candles, int(time.time()), limits,
                       open_trades=open_trades, day_pnl=pnl, payout=payout,
                       trend=trend)

            append(SIGNALS_CSV, {
                "timestamp": now_utc().isoformat(), "symbol": args.symbol,
                "price": f"{price:.4f}", "payout": f"{payout:.4f}",
                "taken": int(d.tradeable),
                "line": d.line.name if d.line else "",
                "direction": d.direction, "reason": d.reason,
                "day_pnl": f"{pnl:.2f}",
                "trend": trend,
            })

            if not d.tradeable:
                log(f"skip - {d.reason}")
            else:
                log(f"SIGNAL {d.reason}")
                if args.dry_run:
                    log("   [DRY RUN] would buy here, not placing an order")
                    d.line.last_trade_epoch = int(time.time())
                    d.line.trades_today += 1
                else:
                    ctype = "CALL" if d.direction > 0 else "PUT"
                    try:
                        p = (await api.proposal(
                            contract_type=ctype, underlying_symbol=args.symbol,
                            amount=current_stake, basis="stake", currency="USD",
                            duration=args.duration, duration_unit="s"))["proposal"]
                        bought = await api.buy(p["id"], float(p["ask_price"]))
                        cid = bought["buy"]["contract_id"]
                        open_trades += 1
                        taken += 1
                        d.line.last_trade_epoch = int(time.time())
                        d.line.trades_today += 1
                        log(f"   BOUGHT {ctype} {cid} stake {current_stake:.2f} "
                            f"(ladder step {ladder_step+1}/{args.martingale_steps or 1}) "
                            f"payout {p['payout']} ({payout:.4f}x, break-even "
                            f"{break_even(payout)*100:.2f}%)")
                        # Try streaming settlement first (fast path), fall back
                        # to profit_table query if the websocket dies mid-wait.
                        # The stream hang was causing every trade to block for
                        # 600s with no log, no ladder update, and no restart.
                        profit = None
                        try:
                            contract = await api.wait_for_settlement(cid, timeout=120)
                            profit = float(contract.get("profit") or 0.0)
                        except Exception as exc:              # noqa: BLE001
                            log(f"   settlement stream failed "
                                f"({type(exc).__name__}) - polling profit table")
                            for _attempt in range(6):
                                await asyncio.sleep(15)
                                try:
                                    pt = await api.send({
                                        "profit_table": 1, "description": 1,
                                        "limit": 10, "sort": "DESC",
                                    })
                                    for tx in pt.get("profit_table", {}).get("transactions", []):
                                        if tx.get("contract_id") == cid:
                                            sell = float(tx.get("sell_price") or 0)
                                            profit = sell - current_stake
                                            break
                                    if profit is not None:
                                        break
                                except Exception:              # noqa: BLE001
                                    pass
                        if profit is None:
                            log(f"   could not settle {cid} - assuming loss, "
                                f"ladder steps up as precaution")
                            profit = -current_stake
                        open_trades -= 1
                        log(f"   SETTLED {profit:+.2f}")
                        append(TRADES_CSV, {
                            "timestamp": now_utc().isoformat(),
                            "symbol": args.symbol, "line": d.line.name,
                            "type": ctype, "stake": f"{current_stake:.2f}",
                            "payout": p["payout"], "profit": f"{profit:.2f}",
                            "contract_id": cid,
                            "ladder_step": ladder_step,
                        })
                        # Martingale ladder: on loss step up, on win reset.
                        if args.martingale_steps > 0:
                            if profit < 0:
                                ladder_step += 1
                                if ladder_step >= args.martingale_steps:
                                    log(f"   ladder exhausted at step "
                                        f"{ladder_step} - resetting to base "
                                        f"${args.stake:.2f}")
                                    ladder_step = 0
                                    current_stake = args.stake
                                else:
                                    current_stake = round(
                                        args.stake * args.martingale_mult ** ladder_step, 2)
                                    log(f"   loss -> ladder step {ladder_step+1}, "
                                        f"next stake ${current_stake:.2f}")
                            elif profit > 0:
                                if ladder_step > 0:
                                    log(f"   win recovered ladder (was step "
                                        f"{ladder_step+1}) - resetting to base "
                                        f"${args.stake:.2f}")
                                ladder_step = 0
                                current_stake = args.stake
                    except Exception as exc:              # noqa: BLE001
                        open_trades = max(0, open_trades - 1)
                        log(f"   order failed ({type(exc).__name__}: {exc})")

            elapsed = time.monotonic() - cycle
            if elapsed < args.poll:
                await asyncio.sleep(args.poll - elapsed)

        pnl = day_pnl(TRADES_CSV, now_utc().strftime("%Y-%m-%d"))
        log(f"session over: {taken} trade(s) taken, day PnL {pnl:+.2f}")
        log(f"every decision is in {SIGNALS_CSV}; trades in {TRADES_CSV}")
    finally:
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
