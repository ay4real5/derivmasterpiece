"""Checks that must pass before this bot is allowed near real money.

Every check here exists because of something that would otherwise fail
silently or expensively:

- **Account type vs DEMO_MODE.** One Deriv PAT reaches both the demo and the
  real account. Nothing in the token says which one you meant, so a typo in
  `.env` is the difference between a paper loss and a real one.
- **Staking must be flat.** `main.py` already refuses non-flat staking on a
  funded account, but it refuses at launch, from inside a background process
  whose stdout nobody is watching. Better to fail here, out loud, before
  anything is funded.
- **Limits proportional to the balance.** `max_daily_loss: 1000` was chosen
  against a 4,600 demo balance. Carried onto a 200 deposit it means "lose
  everything, five times over" - a limit that cannot bind is not a limit.
- **Journal writable.** The supervisor rebuilds the day's PnL from
  `trade_journal.csv`. If that file cannot be written, the daily loss cap
  silently resets to zero on every restart.

Pure functions returning failure strings, so each refusal is unit-testable
without a network or an account.
"""
from __future__ import annotations

import os
from typing import Any

# A daily loss cap larger than this fraction of the live balance is not a
# meaningful cap. 10% is a starting point, not a recommendation.
DEFAULT_MAX_LOSS_FRACTION = 0.10


def account_is_demo(account: dict[str, Any]) -> bool | None:
    """True/False, or None when the payload does not say.

    Deriv's `list_accounts` returns `account_type: "demo" | "real"`. It does
    NOT return `is_virtual`, which the first version of this check guessed at
    and defaulted to False - so every account read as REAL. That produced a
    false refusal on a demo account, and, far worse, would have PASSED
    `DEMO_MODE=false` pointed at a demo account, which is the exact mix-up
    this check exists to catch. `main.py` already matches on `account_type`;
    this now agrees with it.

    `is_virtual` is still honoured as a fallback in case the field returns.
    An unknown answer is never treated as a pass.
    """
    kind = str(account.get("account_type", "")).strip().lower()
    if kind in ("demo", "virtual"):
        return True
    if kind == "real":
        return False
    if "is_virtual" in account:
        return bool(account["is_virtual"])
    return None


def check_account_type(account: dict[str, Any], demo_mode: bool) -> str | None:
    """The account the token resolved to must match what the config intends."""
    is_demo = account_is_demo(account)
    account_id = account.get("account_id", "?")
    if is_demo is None:
        return (f"could not tell whether {account_id} is demo or real "
                f"(no account_type in the response). Refusing rather than "
                f"guessing.")
    if demo_mode and not is_demo:
        return (f"DEMO_MODE=true but the token resolved a REAL account "
                f"({account_id}). Refusing.")
    if not demo_mode and is_demo:
        return (f"DEMO_MODE=false but the token resolved a DEMO account "
                f"({account_id}). Nothing would be traded for real - check "
                f"the token and account id.")
    return None


def check_staking(staking_name: str, demo_mode: bool,
                  config: dict[str, Any] | None = None) -> str | None:
    """Progressive staking on a real account: allowed, but only on purpose.

    This used to be an unconditional refusal. That was a seatbelt we wrote,
    not a Deriv rule - Deriv accepts any stake size sent to it. Whether to
    wear it is the account holder's decision, so it is now an explicit
    opt-in rather than a wall.

    The default is still refusal, because the default should be the safe one
    and because an accidental martingale on real money is exactly the mistake
    worth making hard. `tools/martingale_sim.py` is where the numbers behind
    that default live.
    """
    if demo_mode or staking_name == "flat":
        return None
    if (config or {}).get("i_accept_progressive_staking_on_real") is True:
        return None
    return (f"staking '{staking_name}' on a REAL account needs "
            f"'i_accept_progressive_staking_on_real: true' in config.yaml.\n"
            f"    Run `python tools/martingale_sim.py --bankroll <your deposit> "
            f"--base {'<your stake>'}` first - at a $200 bankroll with a $5 "
            f"base this ladder busted 99.5% of simulated careers.")


def check_limits(risk_cfg: dict[str, Any], balance: float, stake: float,
                 max_loss_fraction: float = DEFAULT_MAX_LOSS_FRACTION) -> list[str]:
    """Limits have to be small enough to actually bind on this balance."""
    problems: list[str] = []
    max_daily_loss = float(risk_cfg.get("max_daily_loss", 0) or 0)

    if max_daily_loss <= 0:
        problems.append("risk.max_daily_loss must be a positive number.")
    elif balance > 0 and max_daily_loss > balance * max_loss_fraction:
        problems.append(
            f"risk.max_daily_loss {max_daily_loss:.2f} is more than "
            f"{max_loss_fraction:.0%} of the {balance:.2f} balance "
            f"(limit {balance * max_loss_fraction:.2f}). A cap that large "
            f"cannot protect the account."
        )
    if max_daily_loss > balance > 0:
        problems.append(
            f"risk.max_daily_loss {max_daily_loss:.2f} exceeds the entire "
            f"{balance:.2f} balance - it can never trigger."
        )
    if stake <= 0:
        problems.append("stake must be positive.")
    elif balance > 0 and stake > balance * 0.05:
        problems.append(
            f"stake {stake:.2f} is over 5% of the {balance:.2f} balance; "
            f"a losing run ends the account quickly at that size."
        )
    return problems


def check_journal_writable(path: str) -> str | None:
    """The daily loss cap depends on this file surviving restarts."""
    target = os.path.abspath(path)
    directory = os.path.dirname(target) or "."
    if os.path.exists(target):
        if not os.access(target, os.W_OK):
            return f"journal {target} exists but is not writable."
        return None
    if not os.path.isdir(directory) or not os.access(directory, os.W_OK):
        return (f"journal directory {directory} is not writable - the daily "
                f"loss cap would reset to zero on every restart.")
    return None


def check_real_money_acknowledged(config: dict[str, Any], demo_mode: bool) -> str | None:
    """A second, independent switch for real money.

    `DEMO_MODE=false` alone is one edit in one file away from live trading.
    Requiring an explicit acknowledgement in config.yaml as well means no
    single mistake can start it.
    """
    if demo_mode:
        return None
    if config.get("i_understand_real_money") is not True:
        return ("DEMO_MODE=false requires 'i_understand_real_money: true' in "
                "config.yaml as a second, deliberate switch. Refusing.")
    return None


def run_checks(config: dict[str, Any], demo_mode: bool, account: dict[str, Any],
               balance: float, staking_name: str,
               max_loss_fraction: float = DEFAULT_MAX_LOSS_FRACTION) -> list[str]:
    """Every failure, so one run shows all of them rather than one per attempt."""
    problems: list[str] = []
    for check in (
        check_account_type(account, demo_mode),
        check_staking(staking_name, demo_mode, config),
        check_real_money_acknowledged(config, demo_mode),
        check_journal_writable(config.get("journal_path", "trade_journal.csv")),
    ):
        if check:
            problems.append(check)
    problems.extend(check_limits(
        config.get("risk", {}), balance, float(config.get("stake", 0) or 0),
        max_loss_fraction,
    ))
    return problems
