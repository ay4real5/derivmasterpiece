"""Six-group, target-based recovery ladder.

Six groups run one at a time, in order 1->2->3->4->5->6->back to 1. Each
group has a fixed stake for its first 4 trades and a profit TARGET it must
reach before advancing to the next group.

Trades 5-10 of a losing run are recalculated live: the stake for trade N
(5<=N<=10) is sized so that a WIN on it alone would take the group's
cumulative profit from wherever it currently sits straight up to the
group's target - which, because every prior loss already lowered that
cumulative profit, automatically recovers the run's losses as part of
reaching the target. It is not "recover the losses" and separately "reach
the target" as two amounts added together - reaching the target from a
lower (loss-reduced) starting point already requires covering the gap the
losses created.

A win that does not yet clear the group's target resets back to trade 1 in
the SAME group. A win that does clear it advances to the next group
(looping 6->1) and resets to trade 1 there - matching "after any winning
trade, the current group resets back to its first trade unless the group
has already reached the required target to move forward."

If trade 10 also loses, the run is EXHAUSTED: this is a real gap in the
spec (it caps trades at 10 but never says what happens if the 10th also
loses), so the conservative default here is to flag it and stop rather
than silently continue an unrecovered loss or loop back to trade 1 as if
nothing happened.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

MAX_TRADES_PER_RUN = 10


@dataclass(frozen=True)
class GroupSpec:
    number: int
    base_stakes: tuple[float, ...]   # trades 1-4, fixed
    profit_target: float


GROUPS: tuple[GroupSpec, ...] = (
    GroupSpec(1, (5.0, 10.0, 20.0, 40.0), 20.0),
    GroupSpec(2, (1.0, 2.0, 4.0, 8.0), 32.0),
    GroupSpec(3, (2.0, 4.0, 8.0, 16.0), 64.0),
    GroupSpec(4, (3.0, 6.0, 9.0, 18.0), 128.0),
    GroupSpec(5, (4.0, 8.0, 12.0, 24.0), 256.0),
    GroupSpec(6, (5.0, 10.0, 15.0, 30.0), 512.0),
)

# The doubling targets above escalate ruin risk ~25x from Group 1 to Group 6,
# because P(a run wipes out) is roughly target / (target + bankroll). Measured
# over 1,500 simulated 60-day careers at a 48% win rate on a 10,000 bankroll
# (`python -m tools.ladder_lab`), holding base stakes and everything else equal:
#
#   targets 20..512   8.4% -> 10.7% ruin,  71% of groups ever completed
#   targets 20..70     8.4%       ruin,   100% of groups completed
#
# Same base stakes, same recovery rule - only the targets differ. The gentle
# set completes every group AND carries the lower ruin, so there is no tradeoff
# being made here, just a worse set of numbers replaced by a better one.
GENTLE_TARGETS: tuple[float, ...] = (20.0, 30.0, 40.0, 50.0, 60.0, 70.0)


def with_targets(targets: Sequence[float],
                 groups: tuple[GroupSpec, ...] = GROUPS) -> tuple[GroupSpec, ...]:
    """The same base-stake sequences with different profit targets."""
    if len(targets) != len(groups):
        raise ValueError(
            f"need {len(groups)} targets, got {len(targets)}")
    return tuple(
        GroupSpec(g.number, g.base_stakes, float(t))
        for g, t in zip(groups, targets)
    )


@dataclass
class GroupState:
    cumulative_profit: float = 0.0
    trade_number: int = 1          # 1-indexed, within the current run
    run_losses: float = 0.0        # stakes lost so far this run (diagnostic)
    wins: int = 0
    losses: int = 0
    # Losses written off by abandoning a run (see EXHAUST_ACTIONS "reset").
    # Kept out of cumulative_profit so the group can still reach its target,
    # but reported so the true cost of abandonment is never hidden.
    abandoned_losses: float = 0.0
    abandoned_runs: int = 0


# What to do when a run loses every rung it is allowed.
#
# "stop"  - the original conservative default: set `exhausted` and never trade
#           again. Safe, but it ends the bot permanently on a streak that is
#           expected to happen roughly every 690 runs at a 48% win rate, so in
#           practice careers died at a median of ~13 days.
# "reset" - abandon the run: bank the loss, clear the group back to a clean
#           slate, and carry on from the first rung. Converts one rare
#           catastrophic run into frequent bounded ones at the same expected
#           value. The written-off amount is recorded in
#           GroupState.abandoned_losses rather than silently forgiven.
EXHAUST_ACTIONS = ("stop", "reset")


# Recovery sizing modes for trades 5+.
#
# "target"   - the original rule: size the trade so one win both clears the
#              deficit AND delivers the group's full profit target. The target
#              term dominates whenever it is large relative to the first four
#              rungs, which is what made Group 6 jump 20x off its 4th rung and
#              made the Group 5/6 ladders cost more than the whole bankroll.
# "breakeven"- size the trade to restore the group to zero only. Targets are
#              then reached by first-rung wins instead of in one leap. Much
#              gentler ladder (Group 5's full 10 rungs fall from ~24,595 to
#              ~3,922) at the cost of a slower climb to each target.
RECOVERY_MODES = ("target", "breakeven")


class GroupLadder:
    """Tracks all 6 groups; exactly one is "current" (active) at a time.

    `groups` and `recovery_mode` are configuration, not state - they are NOT
    persisted by `to_dict`, so the caller must construct with the same settings
    it wants restored. This is deliberate: a design change should apply on the
    next restart rather than being pinned by an old state file.
    """

    def __init__(self, groups: tuple[GroupSpec, ...] = GROUPS,
                 recovery_mode: str = "target",
                 max_trades_per_run: int = MAX_TRADES_PER_RUN,
                 on_exhaust: str = "stop") -> None:
        if recovery_mode not in RECOVERY_MODES:
            raise ValueError(
                f"unknown recovery_mode {recovery_mode!r} - "
                f"choices: {list(RECOVERY_MODES)}")
        if on_exhaust not in EXHAUST_ACTIONS:
            raise ValueError(
                f"unknown on_exhaust {on_exhaust!r} - "
                f"choices: {list(EXHAUST_ACTIONS)}")
        self.groups = groups
        self.recovery_mode = recovery_mode
        self.max_trades_per_run = max_trades_per_run
        self.on_exhaust = on_exhaust
        self.current_group_index = 0   # index into self.groups
        self.states: dict[int, GroupState] = {g.number: GroupState() for g in groups}
        self.exhausted = False   # True once any run ever loses every rung

    @property
    def group(self) -> GroupSpec:
        return self.groups[self.current_group_index]

    @property
    def state(self) -> GroupState:
        return self.states[self.group.number]

    def next_stake(self, payout_multiplier: float) -> float:
        """The stake for the next trade in the current group's run."""
        st = self.state
        n = st.trade_number
        if n <= len(self.group.base_stakes):
            return self.group.base_stakes[n - 1]

        edge = payout_multiplier - 1.0
        if edge <= 0:
            raise ValueError(
                f"payout_multiplier {payout_multiplier} implies no profit on "
                f"a win - cannot size a recovery trade")

        if self.recovery_mode == "breakeven":
            # Restore to zero, nothing more. If the group is somehow already
            # non-negative past rung 4, there is nothing to recover - fall back
            # to the last base stake rather than staking 0 and stalling.
            deficit = max(0.0, -st.cumulative_profit)
            if deficit <= 0:
                return self.group.base_stakes[-1]
            return round(deficit / edge, 2)

        remaining_target = max(0.0, self.group.profit_target - st.cumulative_profit)
        return round(remaining_target / edge, 2)

    def record_result(self, stake: float, profit: float) -> dict:
        """Update state after a trade settles. Returns what happened, for
        logging: group/trade_number/won/profit, plus reached_target and
        advanced_to_group on a target-clearing win, or exhausted on a 10th
        straight loss."""
        if self.exhausted:
            return {"exhausted": True}

        st = self.state
        won = profit > 0
        st.cumulative_profit += profit
        info = {
            "group": self.group.number, "trade_number": st.trade_number,
            "won": won, "stake": stake, "profit": profit,
            "group_cumulative_profit": st.cumulative_profit,
        }

        if won:
            st.wins += 1
            reached_target = st.cumulative_profit >= self.group.profit_target
            info["reached_target"] = reached_target
            st.trade_number = 1
            st.run_losses = 0.0
            if reached_target:
                self.current_group_index = (self.current_group_index + 1) % len(self.groups)
                info["advanced_to_group"] = self.group.number
        else:
            st.losses += 1
            st.run_losses += stake
            if st.trade_number >= self.max_trades_per_run:
                if self.on_exhaust == "reset":
                    # Abandon: write the run's damage off the group's books so
                    # the group starts clean, but never pretend it did not
                    # happen - the account really did lose this.
                    written_off = max(0.0, -st.cumulative_profit)
                    st.abandoned_losses += written_off
                    st.abandoned_runs += 1
                    st.cumulative_profit += written_off   # -> 0.0
                    st.trade_number = 1
                    st.run_losses = 0.0
                    info["abandoned"] = True
                    info["written_off"] = written_off
                else:
                    self.exhausted = True
                    info["exhausted"] = True
            else:
                st.trade_number += 1

        return info

    def status_line(self) -> str:
        st = self.state
        return (f"Group {self.group.number}/{len(self.groups)}, trade "
                f"{st.trade_number}/{self.max_trades_per_run}, run losses "
                f"{st.run_losses:.2f}, group P&L {st.cumulative_profit:.2f}/"
                f"{self.group.profit_target:.2f} target")

    def to_dict(self) -> dict:
        return {
            "current_group_index": self.current_group_index,
            "exhausted": self.exhausted,
            "states": {
                str(num): {
                    "cumulative_profit": s.cumulative_profit,
                    "trade_number": s.trade_number,
                    "run_losses": s.run_losses,
                    "wins": s.wins,
                    "losses": s.losses,
                    "abandoned_losses": s.abandoned_losses,
                    "abandoned_runs": s.abandoned_runs,
                }
                for num, s in self.states.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict | None, **config) -> "GroupLadder":
        """Restore saved STATE. Configuration (`groups`, `recovery_mode`,
        `max_trades_per_run`) comes from `config`, not the state file, so a
        design change takes effect on the next restart."""
        gl = cls(**config)
        if not data:
            return gl
        gl.current_group_index = data.get("current_group_index", 0)
        gl.exhausted = data.get("exhausted", False)
        for num_str, s in data.get("states", {}).items():
            num = int(num_str)
            if num in gl.states:
                gl.states[num] = GroupState(
                    cumulative_profit=s.get("cumulative_profit", 0.0),
                    trade_number=s.get("trade_number", 1),
                    run_losses=s.get("run_losses", 0.0),
                    abandoned_losses=s.get("abandoned_losses", 0.0),
                    abandoned_runs=s.get("abandoned_runs", 0),
                    wins=s.get("wins", 0),
                    losses=s.get("losses", 0),
                )
        return gl
