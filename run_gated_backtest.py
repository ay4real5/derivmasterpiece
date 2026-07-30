"""Pure PDF vs the gated variant, on the same candles, same trade rules.

The gates are logic corrections, not new alpha: ADX is direction-blind, so
letting it push a directional score was incoherent, and trading an average of
a bull and a bear case is not a forecast. This measures what removing those two
incoherences does - and on a random-walk feed the honest expectation is
"roughly nothing to the win rate, fewer trades, so a smaller loss".
"""
from __future__ import annotations

import argparse, asyncio, os
import yaml
from dotenv import load_dotenv

from deriv_bot.api import DerivAPI
from pricebot.pdf_strategy import score_series_detail, signals_from_detail
from pricebot.rise_fall_backtest import break_even_win_rate

SYMS = ["R_10", "R_25", "R_50", "R_75", "R_100"]
PAYOUT = 1.9233
STAKE = 3.0
DURATION_BARS = 5          # 5-minute expiry on 1-minute candles


def run(candles, signals, bars, stake, payout):
    trades = wins = 0
    net = 0.0
    i, n = 0, len(candles)
    while i < n - bars:
        d = signals[i]
        if d == 0:
            i += 1
            continue
        entry = float(candles[i]["close"])
        exit_p = float(candles[i + bars]["close"])
        won = exit_p > entry if d > 0 else exit_p < entry   # exact tie loses
        trades += 1
        wins += 1 if won else 0
        net += stake * (payout - 1.0) if won else -stake
        i += bars + 1
    return trades, wins, net


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles", type=int, default=20000)
    args = ap.parse_args()
    load_dotenv()
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    api = DerivAPI(cfg["app_id"]); token = os.environ["DERIV_API_TOKEN"]
    acct = next(a for a in await api.list_accounts(token)
                if a.get("account_type") == "demo")
    await api.connect(await api.request_trading_ws_url(token, acct["account_id"]))
    variants = {
        "pure PDF":            dict(adx_mode="score", min_adx=0.0,  require_agreement=False),
        "+ADX gate":           dict(adx_mode="gate",  min_adx=20.0, require_agreement=False),
        "+ADX gate +agreement": dict(adx_mode="gate", min_adx=20.0, require_agreement=True),
    }
    totals = {k: [0, 0, 0.0] for k in variants}
    try:
        print(f"break-even win rate at {PAYOUT}x = "
              f"{break_even_win_rate(PAYOUT)*100:.2f}%\n")
        for sym in SYMS:
            cs = await api.candle_history(sym, granularity=60,
                                          count=args.candles, page_pause=0.05)
            detail = score_series_detail(cs)
            print(f"--- {sym}  {len(cs):,} candles ---")
            print(f"{'variant':<22}{'trades':>8}{'win%':>8}{'net':>10}{'per trade':>11}")
            for name, opts in variants.items():
                sig = signals_from_detail(detail, **opts)
                t, w, net = run(cs, sig, DURATION_BARS, STAKE, PAYOUT)
                totals[name][0] += t; totals[name][1] += w; totals[name][2] += net
                wr = w / t * 100 if t else 0
                pt = net / t if t else 0
                print(f"{name:<22}{t:>8}{wr:>7.2f}%{net:>+10.2f}{pt:>+11.4f}")
            print()
    finally:
        await api.close()
    print("="*62)
    print(f"{'TOTAL':<22}{'trades':>8}{'win%':>8}{'net':>10}{'per trade':>11}")
    for name,(t,w,net) in totals.items():
        wr = w/t*100 if t else 0
        print(f"{name:<22}{t:>8}{wr:>7.2f}%{net:>+10.2f}{(net/t if t else 0):>+11.4f}")
    print(f"\nbreak-even needs {break_even_win_rate(PAYOUT)*100:.2f}%")


asyncio.run(main())
