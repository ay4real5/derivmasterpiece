"""Is ANY Deriv contract priced below its exactly-known true probability?

No prediction involved. A digit contract's true win probability is arithmetic:
DIGITEVEN is 0.5, DIGITUNDER 4 is 0.4, DIGITMATCH 7 is 0.1. If Deriv's quoted
payout ever implies a probability BELOW the true one, that contract has
positive expected value on its own - free money that does not care what the
generator does next.

This is the one route that survives the tick battery: it does not need the
feed to be predictable, only the pricing to be wrong somewhere.

implied probability = stake / payout, which INCLUDES the house margin.
edge = true_probability * payout / stake - 1. Positive means take it.
"""
import asyncio, os, yaml
from dotenv import load_dotenv
from deriv_bot.api import DerivAPI

SYMS = ["R_10", "R_25", "R_50", "R_75", "R_100"]
STAKE = 1.0

def legs():
    out = []
    for b in range(10):
        out.append(("DIGITMATCH", str(b), 0.1))
        out.append(("DIGITDIFF", str(b), 0.9))
    for b in range(9):                      # OVER n wins on n+1..9
        out.append(("DIGITOVER", str(b), (9 - b) / 10))
    for b in range(1, 10):                  # UNDER n wins on 0..n-1
        out.append(("DIGITUNDER", str(b), b / 10))
    out.append(("DIGITEVEN", None, 0.5))
    out.append(("DIGITODD", None, 0.5))
    return out

async def main():
    load_dotenv(override=True)
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    api = DerivAPI(cfg["app_id"]); tok = os.environ["DERIV_API_TOKEN"]
    a = next(x for x in await api.list_accounts(tok) if x.get("account_type") == "demo")
    await api.connect(await api.request_trading_ws_url(tok, a["account_id"]))
    rows = []
    try:
        for sym in SYMS:
            for kind, barrier, true_p in legs():
                kw = dict(contract_type=kind, underlying_symbol=sym, amount=STAKE,
                          basis="stake", currency="USD", duration=5,
                          duration_unit="t")
                if barrier is not None:
                    kw["barrier"] = barrier
                try:
                    p = (await api.proposal(**kw))["proposal"]
                    payout = float(p["payout"])
                except Exception:
                    continue
                implied = STAKE / payout
                edge = true_p * payout / STAKE - 1.0
                rows.append((sym, kind, barrier, true_p, payout, implied, edge))
            print(f"  {sym}: {sum(1 for r in rows if r[0]==sym)} quotes", flush=True)
    finally:
        await api.close()

    rows.sort(key=lambda r: -r[6])
    print(f"\n{len(rows)} contracts priced. Ranked by edge, best first:\n")
    print(f"{'symbol':<8}{'contract':<12}{'bar':>4}{'true p':>8}{'payout':>9}"
          f"{'implied':>9}{'edge':>9}")
    print("-"*60)
    for r in rows[:12]:
        print(f"{r[0]:<8}{r[1]:<12}{str(r[2] or ''):>4}{r[3]:>8.2f}{r[4]:>9.2f}"
              f"{r[5]:>9.4f}{r[6]*100:>8.2f}%")
    print("   ...")
    for r in rows[-3:]:
        print(f"{r[0]:<8}{r[1]:<12}{str(r[2] or ''):>4}{r[3]:>8.2f}{r[4]:>9.2f}"
              f"{r[5]:>9.4f}{r[6]*100:>8.2f}%")

    pos = [r for r in rows if r[6] > 0]
    print(f"\nCONTRACTS WITH POSITIVE EDGE: {len(pos)}")
    for r in pos:
        print(f"   {r[0]} {r[1]}:{r[2]} edge {r[6]*100:+.2f}%  <-- FREE MONEY")
    if not pos:
        best = rows[0]
        print(f"   none. The cheapest anywhere is {best[0]} {best[1]}:{best[2]} "
              f"at {best[6]*100:.2f}%")
        print(f"   spread across all {len(rows)}: "
              f"{rows[0][6]*100:.2f}% to {rows[-1][6]*100:.2f}%")

asyncio.run(main())
