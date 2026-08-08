"""Check when next trade might fire, with optional account prefix."""
import csv
import json
import sys

prefix = sys.argv[1] if len(sys.argv) > 1 else ""
signals_file = f"{prefix}_signals.csv" if prefix else "signals.csv"

with open(signals_file, newline='', encoding='utf-8') as f:
    signals = list(csv.DictReader(f))

latest = signals[-1] if signals else None
if latest:
    print(f"Account prefix: {prefix or '(default/account1)'}")
    print(f"Latest signal: {latest['timestamp'][11:19]}  R_50={latest['price']}")
    print(f"Reason: {latest['reason']}")
    print()

price = float(latest['price']) if latest else 0
print('Active levels vs current price:')
with open('lines.json', encoding='utf-8') as f:
    lines = json.load(f)
for ln in lines:
    if ln['active']:
        dist = (price - ln['price_level']) / ln['price_level'] * 100
        in_zone = abs(dist) <= ln['tolerance_pct']
        marker = '<-- IN ZONE' if in_zone else ''
        print(f"  {ln['name']:3s}  {ln['type']:10s}  {ln['price_level']:.4f}  "
              f"{dist:+.3f}%  tol={ln['tolerance_pct']}%  {marker}")

print()
print('Recent signals:')
for s in signals[-8:]:
    ts = s['timestamp'][11:19]
    taken = 'TRADE' if s.get('taken') == '1' else 'skip '
    print(f"  {ts}  {taken}  {s['reason'][:60]}")
