"""Pull longer-timeframe candles from the public WS and find S/R levels.

Uses the public market-data endpoint (no auth needed) to scan R_25.
"""
import asyncio
import json
import websockets

PUBLIC_WS = "wss://ws.derivws.com/websockets/v3?app_id=1089"
SYMBOL = "R_25"


async def get_candles(ws, granularity, count):
    req = {"ticks_history": SYMBOL, "adjust_start_time": 1,
           "count": count, "end": "latest", "style": "candles",
           "granularity": granularity}
    await ws.send(json.dumps(req))
    r = json.loads(await ws.recv())
    return r["candles"]


async def main():
    async with websockets.connect(PUBLIC_WS, open_timeout=20) as ws:
        for label, gran, count in [("1H", 3600, 200), ("10m", 600, 500),
                                    ("5m", 300, 500), ("3m", 180, 500),
                                    ("1m", 60, 500)]:
            try:
                candles = await get_candles(ws, gran, count)
                highs = [float(c["high"]) for c in candles]
                lows = [float(c["low"]) for c in candles]
                closes = [float(c["close"]) for c in candles]
                current = closes[-1]

                swing_highs = []
                swing_lows = []
                for i in range(3, len(candles) - 3):
                    if highs[i] == max(highs[i - 3:i + 4]):
                        swing_highs.append((i, highs[i]))
                    if lows[i] == min(lows[i - 3:i + 4]):
                        swing_lows.append((i, lows[i]))

                print(f"\n=== {label} ({count} candles, ~{count * gran / 3600:.0f}h) ===")
                print(f"Current price: {current:.4f}")
                print(f"Range: {min(lows):.4f} - {max(highs):.4f}")
                print(f"Swing highs found: {len(swing_highs)}")
                print(f"Swing lows found: {len(swing_lows)}")

                def cluster(levels):
                    if not levels:
                        return []
                    levels_sorted = sorted(levels, key=lambda x: x[1])
                    clusters = []
                    current_cluster = [levels_sorted[0]]
                    for lvl in levels_sorted[1:]:
                        if abs(lvl[1] - current_cluster[-1][1]) / current_cluster[-1][1] * 100 < 0.3:
                            current_cluster.append(lvl)
                        else:
                            clusters.append(current_cluster)
                            current_cluster = [lvl]
                    clusters.append(current_cluster)
                    result = []
                    for c in clusters:
                        avg_price = sum(l[1] for l in c) / len(c)
                        touches = len(c)
                        dist_pct = (avg_price - current) / current * 100
                        result.append((avg_price, touches, dist_pct))
                    return result

                print(f"\nResistance levels (above current price), nearest first:")
                res = [c for c in cluster(swing_highs) if c[2] > 0]
                for price, touches, dist in sorted(res, key=lambda x: abs(x[2]))[:5]:
                    print(f"  {price:.4f}  touches={touches}  dist={dist:+.3f}%")

                print(f"\nSupport levels (below current price), nearest first:")
                sup = [c for c in cluster(swing_lows) if c[2] < 0]
                for price, touches, dist in sorted(sup, key=lambda x: abs(x[2]))[:5]:
                    print(f"  {price:.4f}  touches={touches}  dist={dist:+.3f}%")

            except Exception as e:
                print(f"{label}: error - {type(e).__name__}: {e}")


asyncio.run(main())
