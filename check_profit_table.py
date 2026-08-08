"""Pull Deriv's own settled-trade history directly, bypassing the browser UI,
and cross check against our own CSV journal to catch mis-recorded trades."""
import asyncio
import csv
import sys
import yaml
from deriv_bot.api import DerivAPI

cfg = yaml.safe_load(open('config.yaml', encoding='utf-8'))

TOKENS = {
    "account1": ("pat_6d57ffc4003170efe8279c8e0c6ab73cc362c7f4d5b7bd6adace490d4804d2b3",
                 cfg['app_id'], "sr_trades.csv"),
    "ac2": ("pat_0d0c11105972c87e0304d0291851363fd243094a4a257db8ea0aa31acb41f1a2",
            "343GsiWjpyIskHP1nbTzi", "ac2_sr_trades.csv"),
}


async def check(label, token, app_id, csv_path):
    api = DerivAPI(app_id)
    accts = await api.list_accounts(token)
    demo = next(a for a in accts if a.get('account_type') == 'demo')
    url = await api.request_trading_ws_url(token, demo['account_id'])
    await api.connect(url)
    txs = await api.profit_table(limit=30)
    deriv_by_cid = {t['contract_id']: t for t in txs}
    await api.close()

    with open(csv_path, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    print(f"=== {label} ({demo['account_id']}) ===")
    mismatches = []
    for r in rows:
        cid = int(r['contract_id'])
        if cid not in deriv_by_cid:
            continue
        t = deriv_by_cid[cid]
        buy = float(t['buy_price'])
        sell = float(t.get('sell_price') or 0)
        real_profit = round(sell - buy, 2)
        our_profit = float(r['profit'])
        if abs(real_profit - our_profit) > 0.01:
            mismatches.append((cid, r['timestamp'], our_profit, real_profit))
    if mismatches:
        print(f"  MISMATCHES FOUND: {len(mismatches)}")
        for cid, ts, ours, real in mismatches:
            print(f"    contract={cid} ts={ts} our_csv={ours:+.2f} "
                  f"deriv_actual={real:+.2f}")
    else:
        print("  No mismatches in the overlapping contracts.")


async def main():
    for label, (token, app_id, path) in TOKENS.items():
        await check(label, token, app_id, path)

asyncio.run(main())
