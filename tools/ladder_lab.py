"""Simulate a group-ladder design before risking anything on it.

`tools/martingale_sim.py` can only model a hardcoded 2x double-up, so the
6-group ladder that actually runs live has never been simulated at all. This
does that, and adds the three things the older sim ignores: a daily loss cap, a
daily profit target, and per-day bucketing (it simulated one unbounded career).

Two modes:

    MONTE CARLO      i.i.d. coin flips at a fixed win probability.
    HISTORICAL       resample real win/loss sequences from the bot's own
                     sr_trades.csv. Real outcomes CLUSTER - losses arrive in
                     streaks - and clustering is exactly what kills a ladder,
                     so i.i.d. flips understate ruin. Nothing else in this repo
                     feeds a real trade sequence through a staking rule.

What it will not tell you: whether the strategy makes money. Every design here
has the same negative expectation (the house margin is ~3.99% on Rise/Fall).
What differs is HOW LONG you survive and WHAT SHAPE the losing takes, which is
a real choice and the entire point of the exercise.

    python -m tools.ladder_lab --compare
    python -m tools.ladder_lab --recovery breakeven --trials 5000
    python -m tools.ladder_lab --historical sr_trades.csv --trials 5000
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deriv_bot.group_ladder import GROUPS, GroupLadder, GroupSpec  # noqa: E402

# Measured live on R_10/R_25/R_50/R_75 at every duration from 1 tick to 1 hour
# (config.risefall.yaml). Break-even 51.99%, house margin 3.99%.
RISE_FALL_PAYOUT = 1.9233
# Measured across 205 real settled trades on both demo accounts: 48.6% and
# 48.1%. Below the 51.99% break-even, which is why every design loses.
MEASURED_WIN_RATE = 0.48


def load_outcomes(path: str) -> list[bool]:
    """Real win/loss sequence from a bot trade journal, oldest first."""
    with open(path, newline="", encoding="utf-8") as fh:
        return [float(r["profit"]) > 0 for r in csv.DictReader(fh)
                if (r.get("profit") or "").strip()]


def _block_bootstrap(outcomes: list[bool], n: int, rng: random.Random,
                     block: int = 12) -> list[bool]:
    """Resample in contiguous blocks so losing STREAKS survive the resample.

    Drawing single trades independently would destroy the clustering that is
    the whole reason to use real data - it would just reproduce the i.i.d. case
    with extra steps.
    """
    out: list[bool] = []
    while len(out) < n:
        start = rng.randrange(len(outcomes))
        out.extend(outcomes[start:start + block])
    return out[:n]


def simulate(
    *,
    groups: tuple[GroupSpec, ...] = GROUPS,
    recovery_mode: str = "target",
    payout: float = RISE_FALL_PAYOUT,
    win_prob: float = MEASURED_WIN_RATE,
    bankroll: float = 10_000.0,
    max_daily_loss: float | None = 1_300.0,
    target_profit: float | None = 3_000.0,
    trades_per_day: int = 80,
    days: int = 60,
    trials: int = 2_000,
    seed: int = 42,
    outcomes: list[bool] | None = None,
    on_exhaust: str = "stop",
) -> dict:
    """Run `trials` independent careers of `days` days each.

    Ruin = the ladder names a stake the balance cannot cover. That is the real
    failure mode observed live (Account 2 needed 2,907 against 8,402 with
    6,054 and 12,605 still behind it), not a tidy stop-loss.
    """
    rng = random.Random(seed)
    ruined = 0
    finals, drawdowns, days_survived = [], [], []
    group_completions: dict[int, list[int]] = {g.number: [] for g in groups}

    for _ in range(trials):
        gl = GroupLadder(groups=groups, recovery_mode=recovery_mode,
                         on_exhaust=on_exhaust)
        balance = bankroll
        peak = bankroll
        max_dd = 0.0
        trade_no = 0
        completed_at: dict[int, int] = {}
        career_over = False

        seq = (_block_bootstrap(outcomes, days * trades_per_day + 16, rng)
               if outcomes else None)

        for day in range(days):
            day_pnl = 0.0
            for _ in range(trades_per_day):
                if gl.exhausted:
                    career_over = True
                    break
                # Day limits: checked BEFORE placing, exactly as the live bot
                # does in pricebot/sr_lines.py.
                if max_daily_loss is not None and day_pnl <= -abs(max_daily_loss):
                    break
                if target_profit is not None and day_pnl >= target_profit:
                    break

                stake = gl.next_stake(payout)
                if stake > balance:
                    ruined += 1
                    career_over = True
                    break

                won = (seq[trade_no] if seq is not None
                       else rng.random() < win_prob)
                trade_no += 1
                profit = round(stake * (payout - 1.0), 2) if won else -stake
                balance += profit
                day_pnl += profit

                info = gl.record_result(stake, profit)
                if info.get("reached_target"):
                    grp = info["group"]
                    completed_at.setdefault(grp, trade_no)

                peak = max(peak, balance)
                max_dd = max(max_dd, peak - balance)
            if career_over:
                break

        days_survived.append(day + 1)
        finals.append(balance)
        drawdowns.append(max_dd)
        for grp, at in completed_at.items():
            group_completions[grp].append(at)

    def pct(xs: list[float], p: float) -> float:
        if not xs:
            return float("nan")
        return sorted(xs)[min(len(xs) - 1, int(len(xs) * p))]

    return {
        "trials": trials,
        "recovery_mode": recovery_mode,
        "ruin_rate": ruined / trials,
        "median_final": statistics.median(finals),
        "mean_final": statistics.fmean(finals),
        "median_days_survived": statistics.median(days_survived),
        "p10_final": pct(finals, 0.10),
        "p90_final": pct(finals, 0.90),
        "median_max_drawdown": statistics.median(drawdowns),
        "p95_max_drawdown": pct(drawdowns, 0.95),
        "group_completion_rate": {
            g.number: len(group_completions[g.number]) / trials for g in groups
        },
        "median_trades_to_complete": {
            g.number: (statistics.median(group_completions[g.number])
                       if group_completions[g.number] else None)
            for g in groups
        },
    }


def ladder_cost(group: GroupSpec, payout: float, recovery_mode: str,
                max_rungs: int = 10) -> tuple[list[float], float]:
    """The full rung sequence and its total, assuming every rung loses.

    This is what has to fit inside the bankroll for a run to be completable at
    all. Groups 5 and 6 do not fit under "target" mode - that is not bad luck,
    it is arithmetic.
    """
    gl = GroupLadder(groups=(group,), recovery_mode=recovery_mode)
    rungs = []
    for _ in range(max_rungs):
        s = gl.next_stake(payout)
        rungs.append(s)
        gl.record_result(s, -s)
        if gl.exhausted:
            break
    return rungs, sum(rungs)


def _fmt(x: float | None, width: int = 10) -> str:
    return " " * (width - 1) + "-" if x is None else f"{x:>{width},.0f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials", type=int, default=2000)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--trades-per-day", type=int, default=80)
    ap.add_argument("--bankroll", type=float, default=10_000.0)
    ap.add_argument("--win-prob", type=float, default=MEASURED_WIN_RATE)
    ap.add_argument("--payout", type=float, default=RISE_FALL_PAYOUT)
    ap.add_argument("--max-daily-loss", type=float, default=1_300.0)
    ap.add_argument("--target-profit", type=float, default=3_000.0)
    ap.add_argument("--recovery", choices=["target", "breakeven"], default="target")
    ap.add_argument("--historical", default=None,
                    help="CSV trade journal to bootstrap real outcomes from")
    ap.add_argument("--compare", action="store_true",
                    help="run both recovery modes side by side")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    outcomes = None
    if args.historical:
        outcomes = load_outcomes(args.historical)
        rate = sum(outcomes) / len(outcomes)
        print(f"historical: {len(outcomes)} real trades, "
              f"{rate*100:.1f}% win rate, block-bootstrapped\n")

    modes = ["target", "breakeven"] if args.compare else [args.recovery]

    print("=" * 78)
    print("LADDER COST PER GROUP  (every rung loses; must fit the bankroll)")
    print("=" * 78)
    print(f"{'group':<7}{'target':>9}", end="")
    for m in modes:
        print(f"{m + ' total':>16}{'fits?':>8}", end="")
    print()
    for g in GROUPS:
        print(f"{g.number:<7}{g.profit_target:>9,.0f}", end="")
        for m in modes:
            _, total = ladder_cost(g, args.payout, m)
            print(f"{total:>16,.0f}{('yes' if total <= args.bankroll else 'NO'):>8}",
                  end="")
        print()

    for m in modes:
        r = simulate(
            recovery_mode=m, payout=args.payout, win_prob=args.win_prob,
            bankroll=args.bankroll, max_daily_loss=args.max_daily_loss,
            target_profit=args.target_profit, trades_per_day=args.trades_per_day,
            days=args.days, trials=args.trials, seed=args.seed, outcomes=outcomes,
        )
        print()
        print("=" * 78)
        print(f"RECOVERY MODE: {m}    ({args.trials:,} careers x {args.days} days, "
              f"win {args.win_prob*100:.1f}%, bankroll {args.bankroll:,.0f})")
        print("=" * 78)
        print(f"  wiped out            {r['ruin_rate']*100:>8.1f}% of careers")
        print(f"  median days survived {r['median_days_survived']:>8.0f} of {args.days}")
        print(f"  median final balance {r['median_final']:>8,.0f}")
        print(f"  10th / 90th pct      {r['p10_final']:>8,.0f} / {r['p90_final']:,.0f}")
        print(f"  median max drawdown  {r['median_max_drawdown']:>8,.0f}")
        print(f"  95th pct drawdown    {r['p95_max_drawdown']:>8,.0f}")
        print(f"\n  {'group':<7}{'completed':>12}{'median trades to hit target':>32}")
        for g in GROUPS:
            rate = r["group_completion_rate"][g.number] * 100
            med = r["median_trades_to_complete"][g.number]
            print(f"  {g.number:<7}{rate:>11.0f}%{_fmt(med, 32)}")


if __name__ == "__main__":
    main()
