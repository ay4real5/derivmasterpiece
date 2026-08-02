"""Test: does Bybit's option IV track recent realized volatility?

This is THE question that decides whether a vol-straddle strategy works on
crypto options. If IV tracks realized vol tightly (r > 0.9), the edge is
priced and there's no trade. If IV lags realized vol, we can forecast vol
and trade the gap.

Approach:
1. Get 365 days of BTC daily candles from Bybit (public API)
2. Compute realized vol (Parkinson high-low estimator, same as vol_forecast.py)
3. Get current BTC option IV surface from Bybit (markIv in ticker data)
4. Compare current IV against recent realized vol
5. Test persistence of realized vol (does today's vol predict tomorrow's?)

We can't get historical IV from Bybit's API (only current), but we CAN:
- Test if realized vol persistence holds on BTC (the repo tested this on
  Bybit perps and found r=+0.48-0.68, 18/18 survive Bonferroni)
- Compare current IV vs recent realized vol to see if IV is in line
- If IV is far from realized, that's a signal (one direction or the other)
"""
import math
import statistics
import requests
from datetime import datetime, timezone


def fetch_bybit_daily(symbol="BTCUSDT", limit=365):
    """Fetch daily candles from Bybit."""
    r = requests.get("https://api.bybit.com/v5/market/kline", params={
        "category": "linear", "symbol": symbol, "interval": "D", "limit": limit
    }, timeout=15)
    candles = r.json().get("result", {}).get("list", [])
    # Bybit returns newest first; reverse to oldest first
    candles.reverse()
    return [{
        "timestamp": int(c[0]),
        "open": float(c[1]), "high": float(c[2]),
        "low": float(c[3]), "close": float(c[4]),
    } for c in candles]


def parkinson_vol(candle):
    """Range-based vol estimator (same as vol_forecast.py)."""
    hi, lo = candle["high"], candle["low"]
    if hi <= 0 or lo <= 0 or hi < lo:
        return None
    if hi == lo:
        return None
    return math.sqrt(math.log(hi / lo) ** 2 / (4.0 * math.log(2.0)))


def close_to_close_vol(candles):
    """|log return| per candle - the noisy cross-check."""
    closes = [c["close"] for c in candles if c["close"] > 0]
    return [abs(math.log(b / a)) for a, b in zip(closes, closes[1:])]


def autocorrelation(values, lag=1):
    """Lag-1 autocorrelation with p-value."""
    n = len(values)
    if n < 30:
        return {"r": None, "p": None, "n": n}
    mean = statistics.fmean(values)
    num = sum((values[i] - mean) * (values[i + lag] - mean)
              for i in range(n - lag))
    den = sum((v - mean) ** 2 for v in values)
    if den == 0:
        return {"r": 0.0, "p": 1.0, "n": n}
    r = num / den
    # t-statistic and two-tailed p (normal approx)
    se = 1.0 / math.sqrt(n)
    z = r / se
    # Two-tailed p from normal CDF approximation
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2))))
    return {"r": r, "p": p, "n": n, "z": z}


def split_half_test(values):
    """Split-half consistency check (same as vol_forecast.py)."""
    n = len(values)
    h = n // 2
    a = autocorrelation(values[:h])
    b = autocorrelation(values[h:])
    consistent = (a["r"] > 0) == (b["r"] > 0) and min(a["r"], b["r"]) > 0.05
    return {"first": a, "second": b, "consistent": consistent}


def fetch_bybit_option_iv(base_coin="BTC"):
    """Fetch current option IV surface from Bybit."""
    r = requests.get("https://api.bybit.com/v5/market/tickers", params={
        "category": "option", "baseCoin": base_coin
    }, timeout=15)
    items = r.json().get("result", {}).get("list", [])
    # Filter to options with valid IV and reasonable delta
    valid = []
    for item in items:
        try:
            iv = float(item.get("markIv", 0))
            delta = abs(float(item.get("delta", 1)))
            if iv > 0 and delta < 0.9:
                valid.append({
                    "symbol": item["symbol"],
                    "iv": iv,
                    "delta": delta,
                    "mark_price": float(item.get("markPrice", 0)),
                    "underlying": float(item.get("underlyingPrice", 0)),
                })
        except (ValueError, KeyError, TypeError):
            continue
    return valid


def main():
    print("=" * 70)
    print("BYBIT IV vs REALIZED VOL TEST")
    print("=" * 70)

    # 1. Fetch BTC daily candles
    print("\n1. Fetching BTC daily candles from Bybit...")
    candles = fetch_bybit_daily("BTCUSDT", 365)
    print("   Got %d daily candles (%s to %s)" % (
        len(candles),
        datetime.fromtimestamp(candles[0]["timestamp"]/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
        datetime.fromtimestamp(candles[-1]["timestamp"]/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
    ))

    # 2. Compute realized vol series (Parkinson)
    print("\n2. Computing realized volatility (Parkinson high-low)...")
    parkinson_vols = []
    for c in candles:
        v = parkinson_vol(c)
        if v is not None:
            parkinson_vols.append(v)
    print("   %d valid vol observations" % len(parkinson_vols))
    print("   mean: %.4f, median: %.4f, sd: %.4f" % (
        statistics.fmean(parkinson_vols),
        statistics.median(parkinson_vols),
        statistics.pstdev(parkinson_vols),
    ))

    # 3. Test persistence (does today's vol predict tomorrow's?)
    print("\n3. Testing volatility persistence (AR(1))...")
    log_vols = [math.log(v) for v in parkinson_vols if v > 0]
    persist = autocorrelation(log_vols, lag=1)
    print("   r = %.4f, p = %.6f, n = %d, z = %.1f" % (
        persist["r"], persist["p"], persist["n"], persist.get("z", 0)))
    split = split_half_test(log_vols)
    print("   split-half: first r=%.4f, second r=%.4f, consistent=%s" % (
        split["first"]["r"], split["second"]["r"], split["consistent"]))

    # 4. Cross-check with close-to-close vol
    print("\n4. Cross-check with close-to-close vol...")
    cc_vols = close_to_close_vol(candles)
    cc_log = [math.log(v) for v in cc_vols if v > 0]
    cc_persist = autocorrelation(cc_log, lag=1)
    print("   close-to-close r = %.4f, p = %.6f, n = %d" % (
        cc_persist["r"], cc_persist["p"], cc_persist["n"]))

    # 5. Fetch current option IV from Bybit
    print("\n5. Fetching current BTC option IV from Bybit...")
    options = fetch_bybit_option_iv("BTC")
    print("   %d valid options" % len(options))
    if options:
        # Find ATM options (delta near 0.5)
        atm = [o for o in options if 0.35 < o["delta"] < 0.65]
        atm.sort(key=lambda o: abs(o["delta"] - 0.5))
        if atm:
            print("   ATM options (delta ~0.5):")
            for o in atm[:5]:
                print("     %s  IV=%.1f%%  delta=%.2f" % (
                    o["symbol"], o["iv"] * 100, o["delta"]))

        # Get average IV across all ATM options
        if atm:
            avg_iv = statistics.fmean([o["iv"] for o in atm])
            print("   average ATM IV: %.1f%%" % (avg_iv * 100))

        # 6. Compare current IV to recent realized vol
        print("\n6. Comparing current IV to recent realized vol...")
        recent_vols = parkinson_vols[-30:]  # last 30 days
        recent_mean = statistics.fmean(recent_vols)
        recent_median = statistics.median(recent_vols)
        # Annualize: daily Parkinson vol * sqrt(365)
        recent_annual = recent_mean * math.sqrt(365)
        recent_median_annual = recent_median * math.sqrt(365)

        if atm:
            avg_iv = statistics.fmean([o["iv"] for o in atm])
            print("   Recent 30d realized vol (annualized): %.1f%%" % (recent_annual * 100))
            print("   Recent 30d median vol (annualized):   %.1f%%" % (recent_median_annual * 100))
            print("   Current ATM IV:                       %.1f%%" % (avg_iv * 100))
            print("   IV - realized gap:                    %+.1f%%" % ((avg_iv - recent_annual) * 100))
            if avg_iv > recent_annual * 1.1:
                print("   -> IV is ABOVE realized (options overpriced, sell vol)")
            elif avg_iv < recent_annual * 0.9:
                print("   -> IV is BELOW realized (options underpriced, buy vol)")
            else:
                print("   -> IV is in line with realized (edge may be priced)")

    # 7. Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("  Realized vol persistence: r=%.4f (p=%.6f)" % (persist["r"], persist["p"]))
    print("  Split-half consistent: %s" % split["consistent"])
    print("  Close-to-close confirms: r=%.4f" % cc_persist["r"])
    if persist["r"] > 0.3 and split["consistent"]:
        print("  -> Volatility IS predictable on BTC (confirms BYBIT.md finding)")
        print("  -> The edge exists. The question is whether IV prices it.")
    else:
        print("  -> Volatility persistence is weak or inconsistent")
    if options and atm:
        avg_iv = statistics.fmean([o["iv"] for o in atm])
        gap = (avg_iv - recent_annual) * 100
        print("  Current IV vs realized: %+.1f%% gap" % gap)
        if abs(gap) > 5:
            print("  -> LARGE gap between IV and realized - potential trade signal")
        else:
            print("  -> IV tracks realized closely - edge may be priced")


if __name__ == "__main__":
    main()
