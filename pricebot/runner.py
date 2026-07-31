"""Live multiplier session: study candles, open at most one position per
symbol, let Deriv's own take-profit and stop-loss close it.

Deliberately different from the digit bot in three ways, each of which was a
lesson paid for:

1. **No cadence.** The digit bot bought something every 45 seconds and that
   frequency, not the contract choice, was what cost the money. Here a
   position opens only when the strategy fires AND that symbol is flat, so
   trade count is an outcome rather than a setting.

2. **The take-profit is derived from a chosen HOLD TIME, not a chosen
   percentage.** A fixed 0.05% target resolves in 14 minutes on EUR/USD and
   14 seconds on gold - same number, wildly different trade frequency, and
   frequency is the cost. `move_for_hold` inverts the first-passage
   relationship so the hold time is the decision and the distance follows.

3. **The exit lives on Deriv's side.** Take-profit and stop-loss are attached
   at purchase, so a crashed or killed bot cannot strand a position. This
   loop only opens and observes.

What it does NOT do is claim an edge. The expected result of each trade is
minus the commission, and on synthetics that is exact. `never` remains the
default strategy for that reason; anything else is being measured, not
trusted.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any

from deriv_bot.api import DerivAPI
from deriv_bot.journal import TradeJournal

from .instruments import INSTRUMENTS, RISE_FALL, TOUCH, build_proposal, stake_for
from .signals import build_strategy
from .symbol_cost import move_for_hold, realised_vol


async def measure_symbol(api: DerivAPI, symbol: str, granularity: int,
                         count: int = 500) -> tuple[list[dict[str, Any]], float]:
    """Candles plus their annualised volatility. Volatility is measured every
    cycle rather than cached: it is what sets the take-profit distance, and a
    stale figure silently changes the hold time."""
    candles = await api.candles(symbol, granularity=granularity, count=count)
    return candles, realised_vol(candles, granularity)


class Session:
    def __init__(self, api: DerivAPI, config: dict[str, Any], journal: TradeJournal):
        self.api = api
        self.cfg = config
        self.journal = journal
        pb = config.get("pricebot", {})
        # Which product expresses the forecast. Multipliers have no expiry and
        # are closed by their own orders; Rise/Fall is all-or-nothing at a
        # deadline, so for it the horizon IS the trade and none of the
        # take-profit machinery below applies.
        self.instrument: str = str(pb.get("instrument", "multiplier"))
        if self.instrument not in INSTRUMENTS:
            raise ValueError(f"unknown instrument '{self.instrument}' - "
                             f"choices: {list(INSTRUMENTS)}")
        self.symbols: list[str] = list(pb.get("symbols") or [])
        self.granularity: int = int(pb.get("granularity", 60))
        self.hold_seconds: float = float(pb.get("target_hold_seconds", 600))
        self.stake: float = float(pb.get("stake", 50.0))
        self.max_stake: float = float(pb.get("max_stake", self.stake))
        self.poll_seconds: float = float(pb.get("poll_seconds", 20))
        # Explicit contract duration. Ticks especially must be stated rather
        # than derived: "3 ticks" is 6 seconds on R_* and 3 on 1HZ*, so a
        # seconds-based horizon would mean different trades per symbol.
        self.duration = pb.get("duration")
        self.duration_unit = pb.get("duration_unit")
        if self.duration is not None:
            self.duration = int(self.duration)
            if self.duration < 1:
                raise ValueError("duration must be >= 1")
            if self.duration_unit not in ("t", "s", "m", "h", "d"):
                raise ValueError("duration_unit must be one of t/s/m/h/d")
        # Per-SYMBOL staking. A single shared ladder across five symbols with
        # 6-second contracts settling out of order would escalate one market's
        # stake because a different one lost, which is not a recovery of
        # anything. Each symbol runs its own sequence.
        self.staking_cfg = dict(pb.get("staking") or {})
        self.stakers: dict[str, Any] = {}
        strat_cfg = dict(pb.get("strategy", {}))
        self.strategy = build_strategy(strat_cfg.pop("name", "never"), **strat_cfg)
        # contract_id per symbol; a symbol with a live position is skipped so
        # the bot cannot stack positions on one market.
        self.open: dict[str, int] = {}
        # Deriv's multiplier ranges are per-symbol and not similar - R_10 takes
        # [400,1000,...], R_25 [160,400,...], gold [100,200,...]. Fetched once
        # per symbol and cached; a hardcoded list meant gold was rejected on
        # every attempt of the first live session and never traded at all.
        self.multipliers: dict[str, tuple[int, ...]] = {}
        self.commissions: dict[str, float] = {}
        self.opened = 0
        self.settled = 0
        self.realised = 0.0

    def _staker(self, symbol: str):
        """The staker for one symbol, created on first use."""
        if symbol not in self.stakers:
            cfg = dict(self.staking_cfg)
            name = cfg.pop("name", "flat")
            from deriv_bot.staking import build_staker
            self.stakers[symbol] = build_staker(name, **cfg)
        return self.stakers[symbol]

    def _stake_for_symbol(self, symbol: str, signal) -> float:
        """Ladder rung for this symbol, capped by max_stake.

        max_stake is a hard ceiling on top of the ladder, not a replacement
        for it: without one, a config mistake in `reset_after_losses` climbs
        without limit.
        """
        st = self._staker(symbol)
        want = st.stake_for(self.stake, 0.9233, float("inf"))
        if self.max_stake:
            want = min(want, self.max_stake)
        return round(max(0.0, want), 2)

    def _log(self, msg: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{stamp}Z] {msg}", flush=True)

    async def _watch(self, symbol: str, contract_id: int) -> None:
        """Follow one position to settlement and journal it.

        Runs as its own task so several symbols can hold positions at once
        without blocking the scan loop.
        """
        try:
            contract = await self.api.wait_for_settlement(contract_id, timeout=86400)
            profit = float(contract.get("profit") or 0.0)
            self.settled += 1
            self.realised += profit
            # The ladder only means anything if losses are recorded against
            # the same symbol that produced them.
            try:
                self._staker(symbol).record(profit)
            except Exception as exc:   # noqa: BLE001 - staking must not kill the watcher
                self._log(f"staking record failed for {symbol}: {type(exc).__name__}")
            self._log(f"CLOSED  {symbol} {contract_id} profit {profit:+.2f} "
                      f"(session {self.realised:+.2f} over {self.settled} closed)")
            self.journal.record(
                symbol=symbol, contract_type=contract.get("contract_type", ""),
                barrier="", stake=contract.get("buy_price", ""),
                payout=contract.get("payout", ""), profit=profit,
                balance_after="", reason=contract.get("status", ""),
                selector="pricebot",
            )
        except Exception as exc:  # noqa: BLE001 - a lost watcher must not kill the session
            self._log(f"watch failed for {symbol} {contract_id}: "
                      f"{type(exc).__name__}: {exc}")
        finally:
            self.open.pop(symbol, None)

    async def _allowed_multipliers(self, symbol: str) -> tuple[int, ...]:
        if symbol not in self.multipliers:
            av = (await self.api.contracts_for(symbol))["contracts_for"]["available"]
            rng = next((c.get("multiplier_range") for c in av
                        if c.get("contract_type") == "MULTUP"), None)
            self.multipliers[symbol] = tuple(rng or ())
        return self.multipliers[symbol]

    async def _consider(self, symbol: str) -> None:
        if symbol in self.open:
            return
        try:
            candles, vol = await measure_symbol(self.api, symbol, self.granularity)
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the scan
            self._log(f"{symbol}: candles unavailable ({type(exc).__name__})")
            return
        if vol <= 0:
            self._log(f"{symbol}: no measurable volatility, skipping")
            return

        signal = self.strategy.evaluate(candles)
        # A no-view TOUCH signal (direction == 0, positive move: "buy this
        # barrier's win-rate shape, no forecast") is deliberately NOT
        # actionable in the direction/multiplier/rise-fall sense, so the
        # usual `actionable` gate would silently drop every FixedNoTouch
        # signal here before it ever reached build_proposal. See
        # pricebot/instruments.py's matching `no_view` branch.
        no_view_touch = (self.instrument == TOUCH and signal is not None
                         and signal.direction == 0 and signal.expected_move_pct > 0)
        if signal is None or not (signal.actionable or no_view_touch):
            self._log(f"{symbol}: vol {vol:.1%} - no signal")
            return

        if self.instrument == RISE_FALL:
            # No take-profit to size and no leverage to choose: the contract
            # pays on direction at a deadline the STRATEGY set, so overriding
            # the horizon here would silently change what was backtested.
            target = signal.expected_move_pct
            allowed: tuple[int, ...] = ()
        elif self.instrument == TOUCH:
            # Same reasoning as RISE_FALL: the strategy already set the
            # barrier (fixed, or forecast-derived), and there is no
            # leverage to look up - `_allowed_multipliers` queries MULTUP's
            # range, which is meaningless for a Touch contract and was, until
            # this fix, silently gating every Touch trade on it (an empty or
            # unrelated range would skip the trade with "no multipliers
            # offered", a bug that was never exercised because no config had
            # ever set instrument: touch until now).
            target = signal.expected_move_pct
            allowed = ()
        else:
            # The chosen hold time decides the target, not the other way round.
            target = move_for_hold(self.hold_seconds, vol)
            signal.expected_move_pct = target
            try:
                allowed = await self._allowed_multipliers(symbol)
            except Exception as exc:  # noqa: BLE001
                self._log(f"{symbol}: multiplier range unavailable ({type(exc).__name__})")
                return
            if not allowed:
                self._log(f"{symbol}: no multipliers offered, skipping")
                return

        amount = self._stake_for_symbol(symbol, signal)
        if amount <= 0:
            self._log(f"{symbol}: staking returned 0, skipping")
            return
        params = build_proposal(
            signal, self.instrument, symbol, amount,
            allowed_multipliers=allowed or None,
            commission=self.commissions.get(symbol, 0.0),
            duration=self.duration, duration_unit=self.duration_unit,
        )
        if params is None:
            return

        try:
            proposal = (await self.api.proposal(**params))["proposal"]
            # Remember the real commission so the next leverage choice can
            # clear it rather than guessing from zero.
            self.commissions[symbol] = float(proposal.get("commission") or 0.0)
            bought = await self.api.buy(proposal["id"], float(proposal["ask_price"]))
        except Exception as exc:  # noqa: BLE001
            self._log(f"{symbol}: could not open ({type(exc).__name__}: {exc})")
            return

        cid = bought["buy"]["contract_id"]
        self.open[symbol] = cid
        self.opened += 1
        if self.instrument == RISE_FALL:
            # Payout is the number that matters here - it sets the break-even
            # win rate at stake/payout, so it is logged on every trade rather
            # than assumed constant.
            payout = float(proposal.get("payout") or 0.0)
            stake_paid = float(params["amount"])
            be = (stake_paid / payout) if payout else float("nan")
            st = self._staker(symbol)
            rung = getattr(st, "consecutive_losses", 0) + 1
            self._log(
                f"OPEN    {symbol} {params['contract_type']} "
                f"stake {stake_paid:.2f} rung {rung} payout {payout:.2f} "
                f"({payout/stake_paid:.4f}x, break-even {be:.1%}) "
                f"for {params['duration']}{params['duration_unit']} | {signal.reason}"
            )
        elif self.instrument == TOUCH:
            payout = float(proposal.get("payout") or 0.0)
            stake_paid = float(params["amount"])
            self._log(
                f"OPEN    {symbol} {params['contract_type']} barrier {params['barrier']} "
                f"stake {stake_paid:.2f} payout {payout:.2f} "
                f"for {params['duration']}{params['duration_unit']} | {signal.reason}"
            )
        else:
            lo = proposal.get("limit_order", {})
            self._log(
                f"OPEN    {symbol} {params['contract_type']} x{params['multiplier']} "
                f"stake {params['amount']:.2f} comm {proposal.get('commission')} | "
                f"target {target:.4%} (~{self.hold_seconds/60:.0f}m) "
                f"TP {lo.get('take_profit', {}).get('order_amount')} "
                f"SL {lo.get('stop_loss', {}).get('order_amount')} | {signal.reason}"
            )
        asyncio.create_task(self._watch(symbol, cid))

    async def run(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        if self.instrument == RISE_FALL:
            hold = (f"expiry {self.duration}{self.duration_unit}"
                    if self.duration else "expiry set by strategy")
            hold += f" staking={self.staking_cfg.get('name', 'flat')}"
        else:
            hold = f"hold~{self.hold_seconds/60:.0f}m"
        self._log(f"session start | {self.instrument} | symbols={self.symbols} "
                  f"strategy={self.strategy.name} stake={self.stake} {hold} "
                  f"candles={self.granularity}s")
        while time.monotonic() < deadline:
            await asyncio.gather(*(self._consider(s) for s in self.symbols))
            await asyncio.sleep(self.poll_seconds)
        self._log(f"session window over: opened {self.opened}, closed {self.settled}, "
                  f"realised {self.realised:+.2f}, still open {list(self.open)}")
        # Positions left open are safe: their exits are held by Deriv, not here.
