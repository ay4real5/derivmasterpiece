"""Session risk manager — the bot's kill switch.

Trades stop the moment any limit is breached; nothing here auto-resumes,
so a fresh process/day has to be started deliberately.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone


@dataclass
class RiskLimits:
    max_daily_loss: float
    max_consecutive_losses: int
    max_trades: int
    target_profit: float | None = None  # stop (happily) once daily PnL reaches this


class RiskManager:
    def __init__(self, limits: RiskLimits, opening_daily_pnl: float = 0.0):
        """`opening_daily_pnl` is what the day has ALREADY lost or made before
        this process started, and it is what makes `max_daily_loss` mean the
        day rather than the process.

        Without it the cap did not cap. This counter starts at zero in every
        new process, and the supervisor restarts the bot on any crash or
        dropped socket - so each restart silently handed out a fresh
        `max_daily_loss` allowance. Observed live: the supervisor relaunched
        with the day at -521 ("within limits"), and the day ran to -1016.38
        against a 1000 cap while the child was only ~-495 into its own
        budget. Enough restarts and the cap is arbitrarily large.

        The supervisor now passes `day_pnl(journal, utc_today())` here, so a
        child launched at -521 has 479 left rather than 1000.
        `_roll_day_if_needed` still zeroes this at the UTC boundary, which is
        correct: the offset belongs to the day it was measured for, and a
        genuinely new day starts clean.
        """
        self.limits = limits
        self.daily_pnl = float(opening_daily_pnl)
        self.consecutive_losses = 0
        self.trade_count = 0
        self.stopped = False
        self.stop_reason: str | None = None
        self._day = self._today()
        # An offset already past the limit must stop the bot before its first
        # trade, not after one more.
        self._check_limits()

    @staticmethod
    def _today() -> date:
        return datetime.now(timezone.utc).date()

    def _roll_day_if_needed(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self.daily_pnl = 0.0
            self.consecutive_losses = 0
            self.trade_count = 0
            self.stopped = False
            self.stop_reason = None

    def record_trade(self, profit: float) -> None:
        self._roll_day_if_needed()
        self.trade_count += 1
        self.daily_pnl += profit
        self.consecutive_losses = self.consecutive_losses + 1 if profit < 0 else 0
        self._check_limits()

    def _check_limits(self) -> None:
        if self.limits.target_profit is not None and self.daily_pnl >= self.limits.target_profit:
            self._stop(f"profit target reached ({self.daily_pnl:+.2f})")
        elif self.daily_pnl <= -abs(self.limits.max_daily_loss):
            self._stop(f"max daily loss reached ({self.daily_pnl:.2f})")
        elif self.consecutive_losses >= self.limits.max_consecutive_losses:
            self._stop(f"{self.consecutive_losses} consecutive losses")
        elif self.trade_count >= self.limits.max_trades:
            self._stop(f"max trade count reached ({self.trade_count})")

    def _stop(self, reason: str) -> None:
        self.stopped = True
        self.stop_reason = reason

    def can_trade(self) -> bool:
        self._roll_day_if_needed()
        return not self.stopped
