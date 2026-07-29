"""Bybit daily/hourly klines via the PUBLIC endpoint - no key, no account.

Paged backwards with `end`, the same shape as deriv_bot.api.candle_history,
because Bybit caps a single response at 1000 candles.
"""
import json, os, sys, time, urllib.request

BASE = "https://api.bybit.com/v5/market/kline"

def klines(symbol, interval="D", pages=8, category="linear"):
    out, end = {}, None
    for _ in range(pages):
        url = f"{BASE}?category={category}&symbol={symbol}&interval={interval}&limit=1000"
        if end:
            url += f"&end={end}"
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read())
        if data.get("retCode") != 0:
            raise RuntimeError(data.get("retMsg"))
        rows = data["result"]["list"]
        if not rows:
            break
        before = len(out)
        for t, o, h, l, c, v, _q in rows:
            out[int(t)] = {"epoch": int(t) // 1000, "open": float(o),
                           "high": float(h), "low": float(l), "close": float(c),
                           "volume": float(v)}
        oldest = min(int(r[0]) for r in rows)
        if len(out) == before or (end is not None and oldest >= end):
            break
        end = oldest - 1
        time.sleep(0.12)
    return [out[k] for k in sorted(out)]

if __name__ == "__main__":
    os.makedirs("bybit_cache", exist_ok=True)
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]
    for iv, pages in (("60", 22),):
        for s in syms:
            p = f"bybit_cache/{s}_{iv}_long.json"
            if os.path.exists(p):
                continue
            try:
                c = klines(s, iv, pages)
            except Exception as e:
                print(f"  {s} {iv}: {type(e).__name__}: {e}", flush=True)
                continue
            json.dump(c, open(p, "w"))
            span = (c[-1]["epoch"] - c[0]["epoch"]) / 86400 if len(c) > 1 else 0
            print(f"  {s:9s} {iv:>3s}  {len(c):>5} candles  {span:>7.0f} days", flush=True)
