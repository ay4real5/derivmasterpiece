"""Monte Carlo simulation of martingale (double-after-loss) staking.

Deliberately a standalone tool, NOT a strategy in `deriv_bot/`: the live bot
has no stake-progression path by design, because martingale is the single
most reliable way retail accounts are destroyed. This exists so the idea can
be *understood* without being *practised*.

Why it kills: doubling turns many small wins into one enormous hidden bet on
"a long losing streak never happens". On a fair-ish coin a 10-loss streak is
routine over a few thousand bets, and by then the required stake is 1024x the
base. The house edge never changes — martingale just concentrates your entire
bankroll into the tail.

    python tools/martingale_sim.py --trials 1000 --bankroll 10000 --base 10
"""
from __future__ import annotations

import argparse
import random
import statistics


def simulate(trials: int, max_bets: int, bankroll: float, base: float,
             win_prob: float, net_win_mult: float, seed: int = 42) -> dict:
    random.seed(seed)
    ruined, finals, peaks, ruin_bets = 0, [], [], []
    for _ in range(trials):
        bal, stake, peak = bankroll, base, bankroll
        for bet in range(1, max_bets + 1):
            if stake > bal:  # cannot cover the next double — busted in practice
                ruined += 1
                ruin_bets.append(bet)
                break
            if random.random() < win_prob:
                bal += stake * net_win_mult
                stake = base
            else:
                bal -= stake
                stake *= 2
            peak = max(peak, bal)
        finals.append(bal)
        peaks.append(peak)
    return {
        "trials": trials,
        "ruin_rate": ruined / trials,
        "median_bets_to_ruin": statistics.median(ruin_bets) if ruin_bets else None,
        "median_peak": statistics.median(peaks),
        "median_final": statistics.median(finals),
        "mean_final": statistics.mean(finals),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Martingale ruin simulator")
    p.add_argument("--trials", type=int, default=1000)
    p.add_argument("--max-bets", type=int, default=2000)
    p.add_argument("--bankroll", type=float, default=10_000.0)
    p.add_argument("--base", type=float, default=10.0)
    p.add_argument("--win-prob", type=float, default=0.5,
                   help="0.5 for Even/Odd or Rise/Fall; 0.9 for DIGITOVER 0")
    p.add_argument("--net-win", type=float, default=0.92,
                   help="net profit per 1.0 staked on a win (0.92 for the 50%% contracts)")
    args = p.parse_args()

    r = simulate(args.trials, args.max_bets, args.bankroll, args.base,
                 args.win_prob, args.net_win)
    print(f"{r['trials']} careers, up to {args.max_bets} bets, "
          f"${args.bankroll:,.0f} bankroll, ${args.base:.0f} base stake")
    print(f"busted:                  {r['ruin_rate']:.1%}")
    if r["median_bets_to_ruin"]:
        print(f"median bets before bust: {r['median_bets_to_ruin']:.0f}")
    print(f"median PEAK balance:     ${r['median_peak']:,.0f}   <- the seductive part")
    print(f"median FINAL balance:    ${r['median_final']:,.0f}")
    print(f"mean FINAL balance:      ${r['mean_final']:,.0f}")
    print("\nThe peak is why people believe it works. The final is why it doesn't.")


if __name__ == "__main__":
    main()
