"""Accurate 'today' breakdown, keyed by the actual bot clock's date."""
import csv
from datetime import datetime, timezone

today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

for path, label in [("sr_trades.csv", "Account 1"), ("ac2_sr_trades.csv", "Account 2")]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    today = [r for r in rows if r["timestamp"].startswith(today_str)]
    wins = sum(1 for r in today if float(r["profit"]) > 0)
    losses = len(today) - wins
    net = sum(float(r["profit"]) for r in today)
    print(f"{label}: {len(today)} trades today ({today_str}), {wins}W/{losses}L, net {net:+.2f}")
    for r in today:
        print(f"   {r['timestamp'][11:19]}  {r['line']}  stake={r['stake']}  P/L={r['profit']}")
    print()
