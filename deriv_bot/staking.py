"""Stake sizing. Flat by default; recovery-martingale available for DEMO ONLY.

Read this before using `RecoveryMartingale`:

Martingale does not change the house edge — it reshapes the outcome
distribution. It raises the chance of hitting a modest profit target
(measured: ~46% vs ~18% flat, for +650/-800 on Even/Odd) by making small
losses recoverable, and pays for that with a much fatter left tail. Over an
unbounded number of sessions it is the fastest known way to destroy an
account: see `tools/martingale_sim.py` (64% of careers bust; median career
ends at half the starting bankroll).

It is only defensible inside a HARD session stop-loss, which is why this
class requires a `max_total_loss` and refuses to size a bet beyond it.
`main.py` additionally refuses to use it on a real-money account.
"""
from __future__ import annotations


class Staker:
    def stake_for(self, base_stake: float, net_multiplier: float, budget_left: float) -> float:
        raise NotImplementedError

    def record(self, profit: float) -> None:
        raise NotImplementedError


class FlatStake(Staker):
    """Always the base stake. The only sizing the bot uses by default."""

    name = "flat"

    def stake_for(self, base_stake: float, net_multiplier: float, budget_left: float) -> float:
        return min(base_stake, budget_left)

    def record(self, profit: float) -> None:
        return None


class RecoveryMartingale(Staker):
    """Sizes each bet so a single win recovers the current losing run plus
    one base-stake profit, then resets. Capped by the remaining session
    budget — it can never stake more than the hard stop allows.

    Note how badly this interacts with high-probability contracts: on the
    90% contract a win pays only 8.7%, so recovering one $35 loss needs a
    $402 stake, and two losses need ~$4,600. Martingale only stays
    affordable on the ~50/50 contracts, whose margin is nearly double.
    """

    name = "recovery-martingale"

    def __init__(self, max_stake: float | None = None):
        self.max_stake = max_stake
        self.cycle_loss = 0.0

    def stake_for(self, base_stake: float, net_multiplier: float, budget_left: float) -> float:
        wanted = (self.cycle_loss + base_stake * net_multiplier) / net_multiplier
        if self.max_stake is not None:
            wanted = min(wanted, self.max_stake)
        return max(0.0, min(wanted, budget_left))

    def record(self, profit: float) -> None:
        if profit < 0:
            self.cycle_loss += -profit
        else:
            self.cycle_loss = 0.0


STAKERS: dict[str, type[Staker]] = {
    "flat": FlatStake,
    "martingale": RecoveryMartingale,
}


def build_staker(name: str, **kwargs) -> Staker:
    try:
        cls = STAKERS[name]
    except KeyError:
        raise ValueError(f"unknown staking '{name}' — choices: {sorted(STAKERS)}") from None
    return cls(**kwargs)
