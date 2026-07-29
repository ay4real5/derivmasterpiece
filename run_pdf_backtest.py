"""Backtest the PDF's Rise/Fall scoring strategy on the five indices it names."""
from __future__ import annotations

import argparse
import asyncio
import os

import yaml
from dotenv import load_dotenv

from deriv_bot.api import DerivAPI
from pricebot.pdf_strategy import score_series, signals_from_series
from pricebot.rise_fall_backtest import break_even_win_rate

INDICES = {"V10": ("1HZ10V", 5), "V25": ("1HZ25V", 5), "V50": ("1HZ50V", 3),
           "V75": ("1HZ75V", 3), "V100": ("1HZ100V", 2)}


def run(candles, signals, duration_bars, stake, payout_mult):
    """One position at a time, entry at the signal candle's close, exact tie loses."""
    trades = wins = 0
    net = 0.0
    i, n = 0, len(candles)
    while i < n - duration_bars:
        d = signals[i]
        if d == 0:
            i += 1
            continue
        entry = float(candles[i]["close"])
        exit_price = float(candles[i + duration_bars]["close"])
        won = exit_price > entry if d > 0 else exit_price < entry
        trades += 1
        wins += 1 if won else 0
        net += stake * (payout_mult - 1.0) if won else -stake
        i += duration_bars + 1
    return trades, wins, net


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles", type=int, default=20000)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--no-confirm", action="store_true")
    args = ap.parse_args()

    load_dotenv()
    token = os.environ["DERIV_API_TOKEN"]
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    api = DerivAPI(cfg["app_id"])
    acct = next(a for a in await api.list_accounts(token)
                if a.get("account_type") == "demo")
    await api.connect(await api.request_trading_ws_url(token, acct["account_id"]))
    try:
        p = (await api.proposal(contract_type="CALL", underlying_symbol="1HZ50V",
                                amount=args.stake, basis="stake", currency="USD",
                                duration=5, duration_unit="m"))["proposal"]
        pm = float(p["payout"]) / args.stake
        be = break_even_win_rate(pm)
        print(f"live quote: 5m Rise on 1HZ50V pays {pm:.4f}x")
        print(f"BREAK-EVEN win rate: {be:.1%}    PDF claims >= 62%\n")

        tt = tw = 0
        tn = 0.0
        print(f"{'index':<7}{'symbol':<10}{'dur':>5}{'signals':>9}{'trades':>8}"
              f"{'win%':>8}{'vs BE':>8}{'net':>10}")
        for name, (sym, dur) in INDICES.items():
            candles = await api.candle_history(sym, granularity=60,
                                               count=args.candles)
            scores = score_series(candles)
            sigs = signals_from_series(scores, confirm=not args.no_confirm)
            fired = sum(1 for s in sigs if s != 0)
            tr, w, net = run(candles, sigs, dur, args.stake, pm)
            tt += tr
            tw += w
            tn += net
            wr = w / tr if tr else 0.0
            print(f"{name:<7}{sym:<10}{dur:>4}m{fired:>9}{tr:>8}{wr*100:>7.1f}%"
                  f"{(wr-be)*100:>+7.1f}{net:>10.2f}")

        if tt:
            wr = tw / tt
            n_se = (wr * (1 - wr) / tt) ** 0.5
            t = (wr - be) / n_se if n_se else 0.0
            print(f"\nALL FIVE: {tt} trades, {wr:.2%} win rate, net {tn:+.2f}")
            print(f"needed {be:.1%} to break even | PDF claimed >=62% | got {wr:.2%}")
            print(f"that is {(wr-be)*100:+.2f} points vs break-even, t = {t:+.2f}")
            print(f"never traded: 0 trades, net 0.00")
    finally:
        await api.close()


asyncio.run(main())
