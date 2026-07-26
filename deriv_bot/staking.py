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

from .strategy import LowEdgeStrategy, Signal


class Staker:
    def stake_for(self, base_stake: float, net_multiplier: float, budget_left: float) -> float:
        raise NotImplementedError

    def record(self, profit: float) -> None:
        raise NotImplementedError

    def override_signal(self, signal: Signal) -> Signal | None:
        """Return a replacement Signal if the staker wants a different
        contract than the strategy picked, or None to leave it alone.
        Default: never override."""
        return None


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

    `recovery_fraction` decides how much of the losing run a single win
    should claw back (1.0 = all of it, 0.5 = half, 0.0 = flat staking).
    `max_stake_multiple` hard-caps any bet at N x the base stake — without
    it, a long run plus a low-paying contract can demand the entire session
    budget on one bet.

    Measured trade-off (20k sessions, +650/-800, $35 base, mixed contracts):
    full recovery uncapped hits the target 41.9% of the time; a 20x cap
    costs ~2 points (39.6%) and removes the worst single-bet blowups; a 5x
    cap costs a lot more (26.8%). Capping buys smoother losing sessions, not
    better odds — the median session ends at the stop-loss either way.
    """

    name = "recovery-martingale"

    def __init__(self, max_stake: float | None = None, recovery_fraction: float = 1.0,
                 max_stake_multiple: float | None = None):
        if not 0.0 <= recovery_fraction <= 1.0:
            raise ValueError("recovery_fraction must be between 0 and 1")
        if max_stake_multiple is not None and max_stake_multiple < 1:
            raise ValueError("max_stake_multiple must be >= 1")
        self.max_stake = max_stake
        self.recovery_fraction = recovery_fraction
        self.max_stake_multiple = max_stake_multiple
        self.cycle_loss = 0.0

    def stake_for(self, base_stake: float, net_multiplier: float, budget_left: float) -> float:
        wanted = base_stake + self.recovery_fraction * self.cycle_loss / net_multiplier
        if self.max_stake_multiple is not None:
            wanted = min(wanted, self.max_stake_multiple * base_stake)
        if self.max_stake is not None:
            wanted = min(wanted, self.max_stake)
        return max(0.0, min(wanted, budget_left))

    def record(self, profit: float) -> None:
        if profit < 0:
            self.cycle_loss += -profit
        else:
            self.cycle_loss = 0.0


class SmartRecoveryMartingale(RecoveryMartingale):
    """`RecoveryMartingale`, but while a losing cycle is open it REPLACES
    the strategy's contract choice with one of `recovery_contracts`
    (default: the ~50%-tier contracts) instead of whatever the strategy's
    own rotation happened to land on.

    Why: recovering a loss on a 90%-tier contract costs roughly 11x the
    loss (it only pays 8.7%); recovering the same loss on a ~50%-tier
    contract (paying ~92%) costs barely more than the loss itself. This was
    found by inspecting a real session's stakes: every stake over $73 had
    landed on DIGITOVER/DIGITUNDER, purely because that's what the
    strategy's rotation happened to serve up next — not because it was a
    good contract to recover on.

    Measured (20k sessions, +600/-1000, $35 base, 20x cap): blind rotation
    hits the target 46.3% of the time (mean -253); routing recovery to the
    50%-tier contracts hits 53.6% (mean -135). Same worst-case tail either
    way (the cap still caps at the same ceiling) — this improves the
    average outcome and the odds, not the sign of the expectation. Fresh
    bets (no open losing cycle) are untouched, so contract variety away
    from recovery is unaffected.
    """

    name = "smart-recovery-martingale"

    def __init__(self, max_stake: float | None = None, recovery_fraction: float = 1.0,
                 max_stake_multiple: float | None = None,
                 recovery_contracts: list[str] | None = None):
        super().__init__(max_stake, recovery_fraction, max_stake_multiple)
        specs = recovery_contracts or ["DIGITEVEN", "DIGITODD", "CALL", "PUT"]
        self.recovery_legs: list[tuple[str, str | None]] = []
        for spec in specs:
            kind, _, barrier = str(spec).partition(":")
            kind = kind.strip().upper()
            if kind not in LowEdgeStrategy.CONTRACT_TYPES:
                raise ValueError(f"unknown contract_type {kind!r}")
            if kind in LowEdgeStrategy._NO_BARRIER:
                self.recovery_legs.append((kind, None))
            else:
                if barrier == "" or not 0 <= int(barrier) <= 9:
                    raise ValueError(f"{kind} needs a barrier 0-9, e.g. {kind}:0")
                self.recovery_legs.append((kind, str(int(barrier))))
        self._toggle = 0

    def override_signal(self, signal: Signal) -> Signal | None:
        if self.cycle_loss <= 0:
            return None  # fresh bet: leave the strategy's choice alone
        kind, barrier = self.recovery_legs[self._toggle % len(self.recovery_legs)]
        self._toggle += 1
        return Signal(
            kind, barrier,
            f"smart-recovery: switched to {kind}{'' if barrier is None else ':' + barrier} "
            f"to recover ${self.cycle_loss:.2f} cheaply (was {signal.contract_type})",
        )


class DoublingMartingale(Staker):
    """The textbook doubling sequence: stake = base * multiplier**streak,
    where streak is the number of consecutive losses. Default multiplier=2
    gives base=10 -> 10, 20, 40, 80, 160, 320, ... Resets to base on a win.

    This is deliberately DIFFERENT from `RecoveryMartingale`: it doubles a
    fixed amount regardless of which contract the strategy happens to bet,
    rather than computing the exact stake needed to recover the loss on
    THAT contract's real payout. Two honest consequences:

    1. On a contract paying close to 2x (near-50% win probability), pure
       doubling comes close to recovering a losing streak in full on the
       next win — but real payouts here are ~1.92x, not exactly 2x, so
       every recovered cycle still leaves a small residual loss (roughly
       the house margin's worth) rather than a full recovery.
    2. On a mixed rotation, a loss taken on one contract (say a 90%-tier
       one, paying only 8.7%) and "recovered" with a doubled stake on a
       different contract next time will over- or under-shoot the true
       recovery amount — the doubling sequence doesn't know or care what
       actually needs recovering in dollar terms, only how many losses in
       a row there have been.

    `max_stake_multiple` hard-caps the sequence (e.g. 32 stops the climb
    at the 6th step of a 2x sequence: 10,20,40,80,160,320, then flat).
    """

    name = "doubling-martingale"

    def __init__(self, multiplier: float = 2.0, max_stake_multiple: float | None = None):
        if multiplier <= 1.0:
            raise ValueError("multiplier must be > 1")
        if max_stake_multiple is not None and max_stake_multiple < 1:
            raise ValueError("max_stake_multiple must be >= 1")
        self.multiplier = multiplier
        self.max_stake_multiple = max_stake_multiple
        self.consecutive_losses = 0

    def stake_for(self, base_stake: float, net_multiplier: float, budget_left: float) -> float:
        wanted = base_stake * (self.multiplier ** self.consecutive_losses)
        if self.max_stake_multiple is not None:
            wanted = min(wanted, self.max_stake_multiple * base_stake)
        return max(0.0, min(wanted, budget_left))

    def record(self, profit: float) -> None:
        if profit < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0


STAKERS: dict[str, type[Staker]] = {
    "flat": FlatStake,
    "martingale": RecoveryMartingale,
    "smart_recovery": SmartRecoveryMartingale,
    "doubling": DoublingMartingale,
}


def build_staker(name: str, **kwargs) -> Staker:
    try:
        cls = STAKERS[name]
    except KeyError:
        raise ValueError(f"unknown staking '{name}' — choices: {sorted(STAKERS)}") from None
    return cls(**kwargs)
