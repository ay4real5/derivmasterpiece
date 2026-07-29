"""Are Bybit options rich or cheap versus realised volatility?

The variance risk premium is the most robust effect in options markets:
implied volatility sits systematically ABOVE subsequent realised volatility,
because option sellers are carrying tail risk and demand to be paid for it.
Where it exists, the trade is SELLING volatility - which is the opposite of
what a "I can forecast volatility better" strategy assumes, and it does not
require forecasting anything.

WHAT THIS SNAPSHOT CAN AND CANNOT SHOW. IV is observed now; the premium is
defined against volatility realised over the option's REMAINING life, which
has not happened. So this compares IV against TRAILING realised volatility as
a proxy, which is only informative because volatility is strongly persistent
(r=+0.59 at the hourly horizon, measured in BYBIT.md). It is one snapshot: it
can show a large premium is PRESENT today, and it cannot show it is reliable.
Bybit does not serve historical IV publicly - checked, the endpoint returns
empty on every parameter - so a time series has to be collected forward and
cannot be back-filled.
"""
from __future__ import annotations

import json
import math
import statistics
import urllib.request
from datetime import datetime, timezone

HOURS_PER_YEAR = 24 * 365


def fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def option_chain(base: str) -> list[dict]:
    d = fetch("https://api.bybit.com/v5/market/tickers"
              f"?category=option&baseCoin={base}")
    if d.get("retCode") != 0:
        raise RuntimeError(d.get("retMsg"))
    return d["result"]["list"]


def parse_symbol(sym: str) -> tuple[datetime, float, str] | None:
    """'BTC-1AUG26-65000-C-USDT' -> (expiry, strike, 'C')."""
    parts = sym.split("-")
    if len(parts) < 4:
        return None
    try:
        exp = datetime.strptime(parts[1], "%d%b%y").replace(tzinfo=timezone.utc)
        return exp, float(parts[2]), parts[3]
    except ValueError:
        return None


def realised_vol_annualised(candles, hours: int) -> float | None:
    """Annualised realised volatility from the last `hours` hourly candles."""
    closes = [float(c["close"]) for c in candles[-(hours + 1):]
              if float(c.get("close", 0)) > 0]
    if len(closes) < 12:
        return None
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    if len(rets) < 10:
        return None
    return statistics.pstdev(rets) * math.sqrt(HOURS_PER_YEAR)


def atm_iv_by_tenor(chain: list[dict], spot: float,
                    band: float = 0.05) -> dict[int, float]:
    """Median mark IV of near-the-money options, grouped by days to expiry.

    Near-the-money only: IV varies strongly with strike (the smile), so
    averaging across all strikes would measure the smile's shape rather than
    the level of implied volatility.
    """
    now = datetime.now(timezone.utc)
    buckets: dict[int, list[float]] = {}
    for row in chain:
        meta = parse_symbol(row.get("symbol", ""))
        iv = row.get("markIv")
        if meta is None or not iv:
            continue
        exp, strike, _kind = meta
        if abs(strike / spot - 1.0) > band:
            continue
        days = max(0, round((exp - now).total_seconds() / 86400))
        if days <= 0:
            continue
        try:
            buckets.setdefault(days, []).append(float(iv))
        except ValueError:
            continue
    return {d: statistics.median(v) for d, v in sorted(buckets.items()) if v}


def main() -> None:
    print("IMPLIED vs REALISED VOLATILITY ON BYBIT OPTIONS")
    print("Positive premium = options are RICH = the trade is SELLING vol.\n")
    for base, perp in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
        try:
            chain = option_chain(base)
        except Exception as exc:
            print(f"{base}: chain unavailable ({type(exc).__name__}: {exc})")
            continue
        spot = None
        for row in chain:
            if row.get("underlyingPrice"):
                spot = float(row["underlyingPrice"])
                break
        if not spot:
            print(f"{base}: no underlying price")
            continue
        candles = json.load(open(f"bybit_cache/{perp}_60_long.json",
                                 encoding="utf-8"))
        ivs = atm_iv_by_tenor(chain, spot)
        print(f"--- {base}  spot {spot:,.0f}  ({len(chain)} contracts quoted) ---")
        print(f"{'days':>6}{'ATM IV':>10}{'trailing RV':>13}{'premium':>10}"
              f"{'ratio':>8}")
        prem = []
        for days, iv in ivs.items():
            hours = max(24, days * 24)
            rv = realised_vol_annualised(candles, hours)
            if rv is None or rv <= 0:
                continue
            prem.append(iv - rv)
            print(f"{days:>6}{iv*100:>9.1f}%{rv*100:>12.1f}%"
                  f"{(iv-rv)*100:>+9.1f}%{iv/rv:>8.2f}")
        if prem:
            print(f"\n  median premium {statistics.median(prem)*100:+.1f} "
                  f"volatility points across {len(prem)} tenors")
        print()

    # A long-run anchor: how does today's realised vol compare with its own
    # history? A snapshot taken in an unusually calm week would overstate the
    # premium, so the context matters.
    print("CONTEXT - is today's realised volatility unusual?")
    for perp in ("BTCUSDT", "ETHUSDT"):
        cs = json.load(open(f"bybit_cache/{perp}_60_long.json", encoding="utf-8"))
        now30 = realised_vol_annualised(cs, 30 * 24)
        window = 30 * 24
        hist = [realised_vol_annualised(cs[i:i + window + 1], window)
                for i in range(0, len(cs) - window, window)]
        hist = [h for h in hist if h]
        if not hist or not now30:
            continue
        rank = sum(1 for h in hist if h < now30) / len(hist)
        print(f"  {perp}: last 30d RV {now30*100:.1f}%  vs history median "
              f"{statistics.median(hist)*100:.1f}%  -> {rank*100:.0f}th percentile "
              f"of {len(hist)} windows")


if __name__ == "__main__":
    main()
