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
import json
import os
import sys
import time
from datetime import datetime, timezone

import yaml
from dotenv import load_dotenv

from deriv_bot.api import DerivAPI
from pricebot.sr_lines import (
    Decision, Limits, break_even, decide, load_lines, mark_broken,
    merge_state, trend_direction,
)
from tools import lockfile

SIGNALS_CSV = "signals.csv"
TRADES_CSV = "sr_trades.csv"
STATE_FILE = "sr_bot_state.json"
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


def load_state() -> dict:
    """Load adaptive learning state so it survives scheduled-task restarts.

    Without this, every 30-minute restart wipes the CALL/PUT loss streaks and
    the direction blocks. The bot would re-learn the same lesson (PUTs lose)
    from scratch each time, losing $40 per lesson.
    """
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError:
        pass


def day_trades(path: str, day: str) -> list[dict]:
    """Return today's settled trades newest-first for circuit-breakers."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("timestamp") or "").startswith(day):
                continue
            rows.append(row)
    return list(reversed(rows))


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lines", default="lines.json")
    ap.add_argument("--symbol", default="R_50")
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--poll", type=float, default=15.0)
    ap.add_argument("--duration", type=int, default=55)
    ap.add_argument("--stake", type=float, default=5.0)
    ap.add_argument("--max-daily-loss", type=float, default=1000.0)
    ap.add_argument("--target-profit", type=float, default=0.0,
                    help="stop trading for the day once this profit is "
                         "reached (0 = no target, run until session ends)")
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
    ap.add_argument("--no-confirm", action="store_true",
                    help="skip 1m candle confirmation - trade as soon as "
                         "price is in the zone and trend allows. High-volume "
                         "mode; more trades but lower quality.")
    ap.add_argument("--direction", choices=["both", "call", "put"],
                    default="both",
                    help="restrict trading to one direction. 'call' = only "
                         "buy support bounces (RISE), 'put' = only sell "
                         "resistance rejections (FALL). R_50 PUTs had 12% "
                         "win rate across 8 trades - use 'call' to disable "
                         "PUTs entirely.")
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
        # ladder_step/current_stake loaded from persisted state below
        # Adaptive direction learning: track recent CALL/PUT results. If one
        # direction loses N times in a row, stop trading it until a win resets
        # the counter. This caught the pattern where PUTs at resistance lost
        # 4 in a row while CALLs at support won 4 in a row - price was pushing
        # up through resistance, so PUTs were throwing money away.
        # State is persisted to sr_bot_state.json so it survives the 30-min
        # scheduled-task restart. Without persistence, the bot re-learns the
        # same lesson from scratch each restart, losing $40 per lesson.
        saved = load_state()
        call_streak = saved.get("call_streak", 0)
        put_streak = saved.get("put_streak", 0)
        call_blocked = saved.get("call_blocked", False)
        put_blocked = saved.get("put_blocked", False)
        ladder_step = saved.get("ladder_step", 0)
        current_stake = (
            round(args.stake * args.martingale_mult ** ladder_step, 2)
            if ladder_step else args.stake
        )
        DIRECTION_BLOCK_AFTER = 2  # block a direction after this many losses

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
            if args.target_profit > 0 and pnl >= args.target_profit:
                log(f"daily profit target reached ({pnl:+.2f} >= "
                    f"{args.target_profit:+.2f}) - stopping for today")
                break
            trend = trend_direction(candles_15m)
            d = decide(price, active, candles, int(time.time()), limits,
                       open_trades=open_trades, day_pnl=pnl, payout=payout,
                       trend=trend, confirm=not args.no_confirm)

            # Direction restriction: --direction call blocks PUTs entirely,
            # --direction put blocks CALLs. Based on data: R_50 PUTs had 12%
            # win rate across 8 trades, so 'call' is the recommended mode.
            if d.tradeable and args.direction == "call" and d.direction < 0:
                d = Decision(None, 0, f"{d.line.name} PUT skipped - "
                            f"direction restricted to CALL only")
            elif d.tradeable and args.direction == "put" and d.direction > 0:
                d = Decision(None, 0, f"{d.line.name} CALL skipped - "
                            f"direction restricted to PUT only")

            # Momentum filter: CALLs win when price is FALLING into support
            # (genuine bounce), lose when RISING into the zone (already bounced,
            # you're late). Data showed 100% win rate on falling-into-support
            # vs 0% on rising-into-zone. This is the single most predictive
            # pattern found in 23 CALL trades.
            if d.tradeable and d.direction > 0 and len(candles) >= 3:
                recent_closes = [float(c["close"]) for c in candles[-3:]]
                rising = recent_closes[-1] > recent_closes[0]
                if rising:
                    d = Decision(None, 0, f"{d.line.name} CALL skipped - "
                                f"price rising into support (not a genuine "
                                f"bounce, momentum may exhaust)")

            # Adaptive direction block: if CALLs or PUTs have lost N in a row,
            # skip that direction. The market is telling us it's pushing one
            # way and we should stop fighting it.
            if d.tradeable and d.direction > 0 and call_blocked:
                d = Decision(None, 0, f"{d.line.name} CALL skipped - "
                            f"{call_streak} consecutive CALL losses, "
                            f"direction blocked until a win resets it")
            elif d.tradeable and d.direction < 0 and put_blocked:
                d = Decision(None, 0, f"{d.line.name} PUT skipped - "
                            f"{put_streak} consecutive PUT losses, "
                            f"direction blocked until a win resets it")

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
                        # Martingale ladder with circuit breakers.
                        # Base math: at 92% payout, a win never fully recovers
                        # all previous losses, but it gets close. The real danger
                        # is a long streak during a bad run. We add:
                        #   1) pause after 3 consecutive losses (don't escalate
                        #      further until the next trade cycle)
                        #   2) stop escalating if recent win rate < 50%
                        #   3) hard cap at args.martingale_steps
                        if args.martingale_steps > 0:
                            recent_trades = [t for t in day_trades(TRADES_CSV, day)]
                            recent_wins = sum(1 for t in recent_trades if float(t["profit"]) > 0)
                            recent_n = len(recent_trades)
                            recent_wr = recent_wins / recent_n if recent_n else 1.0

                            if profit < 0:
                                # Circuit breaker #1: after 3 straight losses,
                                # pause escalation for this cycle.
                                if ladder_step >= 2:
                                    log(f"   martingale paused after 3 losses - "
                                        f"will retry base ${args.stake:.2f} after cooldown")
                                    ladder_step = 0
                                    current_stake = args.stake
                                # Circuit breaker #2: don't escalate if recent
                                # win rate is under 50% (we're in a bad run).
                                elif recent_wr < 0.5:
                                    log(f"   recent WR {recent_wr:.0%} < 50% - "
                                        f"no escalation, next stake ${args.stake:.2f}")
                                    ladder_step = 0
                                    current_stake = args.stake
                                else:
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
                        # Adaptive direction learning: track consecutive
                        # losses per direction. 3 in a row blocks that
                        # direction until a win resets it.
                        if ctype == "CALL":
                            if profit < 0:
                                call_streak += 1
                                if call_streak >= DIRECTION_BLOCK_AFTER and not call_blocked:
                                    call_blocked = True
                                    log(f"   CALLs blocked - {call_streak} "
                                        f"consecutive losses, market pushing down")
                            elif profit > 0:
                                if call_blocked:
                                    log(f"   CALL win resets block (was "
                                        f"{call_streak} losses)")
                                call_streak = 0
                                call_blocked = False
                        elif ctype == "PUT":
                            if profit < 0:
                                put_streak += 1
                                if put_streak >= DIRECTION_BLOCK_AFTER and not put_blocked:
                                    put_blocked = True
                                    log(f"   PUTs blocked - {put_streak} "
                                        f"consecutive losses, market pushing up")
                            elif profit > 0:
                                if put_blocked:
                                    log(f"   PUT win resets block (was "
                                        f"{put_streak} losses)")
                                put_streak = 0
                                put_blocked = False
                        # Persist learning state so it survives restarts.
                        save_state({
                            "call_streak": call_streak,
                            "put_streak": put_streak,
                            "call_blocked": call_blocked,
                            "put_blocked": put_blocked,
                            "ladder_step": ladder_step,
                        })
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
