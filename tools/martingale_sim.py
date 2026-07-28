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
             win_prob: float, net_win_mult: float, seed: int = 42,
             flat: bool = False) -> dict:
    """`flat=True` keeps every bet at `base` instead of doubling after a loss.

    The comparison is the point. On its own, "the ladder busts 80% of
    careers" invites the reply "so what does anything else do?" - and the
    honest answer matters: flat staking on the same negative edge does not
    bust nearly as often, it bleeds steadily instead. Same house edge, very
    different shape, and nobody should have to take that on trust.
    """
    random.seed(seed)
    ruined, finals, peaks, ruin_bets = 0, [], [], []
    for _ in range(trials):
        bal, stake, peak = bankroll, base, bankroll
        for bet in range(1, max_bets + 1):
            if stake > bal:  # cannot cover the next bet — busted in practice
                ruined += 1
                ruin_bets.append(bet)
                break
            if random.random() < win_prob:
                bal += stake * net_win_mult
                stake = base
            else:
                bal -= stake
                stake = base if flat else stake * 2
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
    p.add_argument("--flat", action="store_true",
                   help="also simulate FLAT staking on identical inputs, for comparison")
    args = p.parse_args()

    def show(label: str, r: dict) -> None:
        print(f"\n{label}")
        print(f"  busted:                  {r['ruin_rate']:.1%}")
        if r["median_bets_to_ruin"]:
            print(f"  median bets before bust: {r['median_bets_to_ruin']:.0f}")
        print(f"  median PEAK balance:     ${r['median_peak']:,.0f}")
        print(f"  median FINAL balance:    ${r['median_final']:,.0f}")
        print(f"  mean FINAL balance:      ${r['mean_final']:,.0f}")

    print(f"{args.trials} careers, up to {args.max_bets} bets, "
          f"${args.bankroll:,.0f} bankroll, ${args.base:.0f} base stake, "
          f"win prob {args.win_prob:.0%}")

    ladder = simulate(args.trials, args.max_bets, args.bankroll, args.base,
                      args.win_prob, args.net_win)
    show("DOUBLING LADDER", ladder)

    if args.flat:
        # Same seed, same inputs: the only difference is the staking rule.
        flat = simulate(args.trials, args.max_bets, args.bankroll, args.base,
                        args.win_prob, args.net_win, flat=True)
        show("FLAT STAKE", flat)
        print(f"\nbust rate: ladder {ladder['ruin_rate']:.1%} vs flat "
              f"{flat['ruin_rate']:.1%}")
        print(f"median final: ladder ${ladder['median_final']:,.0f} vs flat "
              f"${flat['median_final']:,.0f}")
        print("\nSame house edge either way - both lose on average. The ladder\n"
              "concentrates the loss into rare large events; flat spreads it out.\n"
              "Which you prefer is a real choice; that you lose on average is not.")
    else:
        print("\nThe peak is why people believe it works. The final is why it doesn't.")
        print("Add --flat to compare against flat staking on identical inputs.")


if __name__ == "__main__":
    main()
