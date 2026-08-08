"""Check current trade status accurately, with optional account prefix."""
import csv
import sys

prefix = sys.argv[1] if len(sys.argv) > 1 else ""
trades_file = f"{prefix}_sr_trades.csv" if prefix else "sr_trades.csv"

with open(trades_file, newline='', encoding='utf-8') as f:
    trades = list(csv.DictReader(f))

today = [t for t in trades if t['timestamp'] >= '2026-08-06']
print(f'Account prefix: {prefix or "(default/account1)"}')
print(f'Today total: {len(today)} trades')
print()

phase1 = [t for t in today if float(t['stake']) == 40]
if phase1:
    print('$40 flat phase:')
    for t in phase1:
        ts = t['timestamp'][11:19]
        profit = float(t['profit'])
        result = 'WIN' if profit > 0 else 'LOSS'
        print(f'  {ts}  {t["line"]}  stake={float(t["stake"]):.0f}  P/L={profit:+.2f}  [{result}]')
    net1 = sum(float(t['profit']) for t in phase1)
    print(f'  Phase net: {net1:+.2f}')
    print()

phase2 = [t for t in today if float(t['stake']) != 40]
if phase2:
    print('$5 martingale phase:')
    wins = 0
    losses = 0
    for t in phase2:
        ts = t['timestamp'][11:19]
        profit = float(t['profit'])
        result = 'WIN' if profit > 0 else 'LOSS'
        if profit > 0:
            wins += 1
        else:
            losses += 1
        print(f'  {ts}  {t["line"]}  stake={float(t["stake"]):.0f}  P/L={profit:+.2f}  [{result}]')
    net2 = sum(float(t['profit']) for t in phase2)
    print(f'  Phase net: {net2:+.2f}')
    print(f'  Wins: {wins}, Losses: {losses}')

print()
total = sum(float(t['profit']) for t in today)
print(f'Overall today net: {total:+.2f}')
