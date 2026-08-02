"""Candles for the S/R backtest: 1m execution, 5m and 10m for the lines."""
import asyncio, os, json, yaml
from dotenv import load_dotenv
from deriv_bot.api import DerivAPI

SYM = "R_50"   # V50

async def main():
    load_dotenv(override=True)
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    api = DerivAPI(cfg["app_id"]); tok = os.environ["DERIV_API_TOKEN"]
    a = next(x for x in await api.list_accounts(tok) if x.get("account_type") == "demo")
    await api.connect(await api.request_trading_ws_url(tok, a["account_id"]))
    os.makedirs("sr_cache", exist_ok=True)
    try:
        for gran, n, label in ((60, 20000, "1m"), (300, 8000, "5m"), (600, 5000, "10m")):
            p = f"sr_cache/{SYM}_{label}.json"
            if os.path.exists(p):
                print(f"  {label}: cached"); continue
            c = await api.candle_history(SYM, granularity=gran, count=n, page_pause=0.05)
            json.dump(c, open(p, "w"))
            span = (c[-1]["epoch"] - c[0]["epoch"]) / 86400
            print(f"  {label}: {len(c):,} candles, {span:.1f} days", flush=True)
    finally:
        await api.close()

asyncio.run(main())
