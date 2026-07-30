"""Is the bot that is RUNNING the bot the config describes?

This module exists because it wasn't, and nothing noticed. Three config changes
- the 700/700 caps, the 3-tick expiry, the recovery ladder - were committed,
tested, and reported as live while the process actually trading kept the old
5-minute flat-stake settings for over an hour.

Nothing lied. Every individual step reported success:

  1. The kill loop matched on Win32_Process.CommandLine, which comes back NULL
     for task-owned processes from a non-elevated session. It matched zero
     processes and said nothing.
  2. Start-ScheduledTask then returned 2147946720 (0x80070420, "an instance of
     this task is already running") because MultipleInstances is IgnoreNew.
     Nobody checked the return code.
  3. Deleting the lock file while a supervisor ran disarmed the single-instance
     guard, because the lock is only consulted at startup - which let a manual
     test session trade in PARALLEL with the task-owned bot.

So the deployed state has to be READ BACK from the log rather than inferred
from the config, and a restart has to be proven rather than assumed. That is
all this module does, in pure functions so the logic is testable without a
scheduler, a network, or a broker.
"""
from __future__ import annotations

import re
from typing import Any

# Windows Task Scheduler results, decoded. The two that matter here both look
# like enormous meaningless integers in PowerShell output.
TASK_RESULTS = {
    0: "ok - last run completed successfully",
    267009: "currently running (0x00041301)",
    267011: "has not yet run (0x00041303)",
    267014: "was terminated by the user (0x00041306)",
    2147942402: "file not found (0x80070002) - check the exe path",
    2147942405: "access denied (0x80070005) - needs elevation",
    2147946720: "ALREADY RUNNING (0x80070420) - the restart did nothing",
}


def decode_task_result(code: int | None) -> str:
    """English for a scheduler result code, or the hex if it is unknown.

    Returning the hex rather than "unknown error" matters: the hex is
    searchable, and 0x80070420 is exactly the code that silently turned a
    restart into a no-op.
    """
    if code is None:
        return "no result recorded"
    if code in TASK_RESULTS:
        return TASK_RESULTS[code]
    unsigned = code & 0xFFFFFFFF
    return f"unrecognised result 0x{unsigned:08X} ({code})"


def restart_succeeded(code: int | None) -> bool:
    """Did a Start-ScheduledTask call actually start something new?

    0 and "currently running" are fine. ALREADY RUNNING is not - that is the
    case that looked like success and was not.
    """
    return code in (0, 267009)


_SESSION = re.compile(
    r"session start \| (?P<instrument>\S+) \|.*?"
    r"strategy=(?P<strategy>\S+)\s+stake=(?P<stake>[\d.]+)\s+"
    r"(?P<terms>.*?)\s+candles=")
_SUPERVISOR = re.compile(
    r"supervisor up \| config=(?P<config>\S+)\s+cap=(?P<cap>\S+)\s+"
    r"target=(?P<target>\S+)\s+session=(?P<session>\S+)")
_STAMP = re.compile(r"\[(?P<stamp>[0-9T:\-]+Z)\]")


def parse_effective_settings(log_text: str) -> dict[str, Any] | None:
    """What the MOST RECENT child session is actually trading, or None.

    Read from the log rather than the config on purpose. The config is the
    intent; this is the fact. When they disagree, a stale child is running and
    that is the whole bug this module is about.
    """
    last = None
    for m in _SESSION.finditer(log_text):
        last = m
    if last is None:
        return None
    terms = last.group("terms")
    expiry = None
    em = re.search(r"expiry (\S+)", terms)
    if em and em.group(1) != "set":
        expiry = em.group(1)
    staking = None
    sm = re.search(r"staking=(\S+)", terms)
    if sm:
        staking = sm.group(1)
    return {
        "instrument": last.group("instrument"),
        "strategy": last.group("strategy"),
        "stake": float(last.group("stake")),
        "expiry": expiry,           # None means "derived from the strategy"
        "staking": staking,         # None means the field predates staking
    }


def parse_supervisor_settings(log_text: str) -> dict[str, Any] | None:
    """The most recent supervisor's caps, as it actually recorded them."""
    last = None
    for m in _SUPERVISOR.finditer(log_text):
        last = m
    if last is None:
        return None

    def num(v: str) -> float | None:
        try:
            return float(v)
        except ValueError:
            return None

    return {"config": last.group("config"), "cap": num(last.group("cap")),
            "target": num(last.group("target")),
            "session_minutes": num(last.group("session").rstrip("m"))}


def observed_expiries(log_text: str) -> dict[str, int]:
    """Every expiry actually traded, counted. The ground truth.

    `grep -c "for 3t"` returning 0 while the config said 3 ticks is what
    revealed the problem, so it gets a function.
    """
    out: dict[str, int] = {}
    for m in re.finditer(r"\bfor (\d+[tsmhd])\b", log_text):
        out[m.group(1)] = out.get(m.group(1), 0) + 1
    return out


def observed_rungs(log_text: str) -> dict[int, int]:
    """Ladder rungs actually staked, counted. Empty means no ladder ran."""
    out: dict[int, int] = {}
    for m in re.finditer(r"\brung (\d+)\b", log_text):
        k = int(m.group(1))
        out[k] = out.get(k, 0) + 1
    return out


def restart_verified(before_text: str, after_text: str) -> bool:
    """Did a NEW supervisor session appear after the restart?

    Counting `supervisor up` occurrences rather than comparing timestamps: a
    restart within the same clock second is otherwise indistinguishable from
    no restart at all.
    """
    before = len(_SUPERVISOR.findall(before_text))
    after = len(_SUPERVISOR.findall(after_text))
    return after > before


def config_matches_running(cfg: dict[str, Any],
                           running: dict[str, Any] | None) -> list[str]:
    """Every way the intended config and the running child disagree.

    Empty list means the deploy took. A non-empty list is the report that was
    missing when three changes silently failed to land.
    """
    pb = (cfg or {}).get("pricebot", {})
    if running is None:
        return ["no session start found in the log - nothing is running, "
                "or the log was rotated"]
    problems: list[str] = []

    want_dur = pb.get("duration")
    want_unit = pb.get("duration_unit")
    want_expiry = f"{want_dur}{want_unit}" if want_dur and want_unit else None
    if want_expiry != running.get("expiry"):
        problems.append(f"expiry: config wants {want_expiry or 'strategy-derived'}, "
                        f"running has {running.get('expiry') or 'strategy-derived'}")

    want_stake = pb.get("stake")
    if want_stake is not None and float(want_stake) != running.get("stake"):
        problems.append(f"stake: config wants {want_stake}, "
                        f"running has {running.get('stake')}")

    want_staking = (pb.get("staking") or {}).get("name", "flat")
    if want_staking != (running.get("staking") or "flat"):
        problems.append(f"staking: config wants {want_staking}, "
                        f"running has {running.get('staking') or 'flat'}")

    want_instrument = pb.get("instrument", "multiplier")
    if want_instrument != running.get("instrument"):
        problems.append(f"instrument: config wants {want_instrument}, "
                        f"running has {running.get('instrument')}")
    return problems


def caps_match_running(want_cap: float, want_target: float,
                       running: dict[str, Any] | None) -> list[str]:
    """The caps the supervisor is actually enforcing versus the ones asked for."""
    if running is None:
        return ["no 'supervisor up' line found - cannot tell what caps apply"]
    problems = []
    if running.get("cap") != want_cap:
        problems.append(f"loss cap: wanted {want_cap}, "
                        f"running enforces {running.get('cap')}")
    if running.get("target") != want_target:
        problems.append(f"profit target: wanted {want_target}, "
                        f"running enforces {running.get('target')}")
    return problems
