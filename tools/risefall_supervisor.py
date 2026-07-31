"""Keep the Rise/Fall bot running, and stop it when the day's loss cap is hit.

DELIBERATELY SEPARATE from tools/supervisor.py. That one is wired to the digit
bot - `main.py scan-trade`, the recovery ladder, the ladder-fits-daily-loss
preflight - and it has been running unattended for days. Adding a second mode
to it would put working code at risk to save a file, so this is its own loop.

What it borrows rather than reimplements is `day_pnl`, because the one lesson
that cost real money on the digit bot applies identically here: a restarting
child starts its own PnL at zero, so a cap enforced inside the child caps the
SESSION, not the DAY. The digit bot reached -1016 against a -1000 cap that
way. Here the cap is checked in the supervisor, from the journal, which is the
only place that knows the whole day.

Rise/Fall needs no ladder preflight - the stake is flat - so this loop is much
smaller than the digit bot's. The child is `run_pricebot.py`, which opens
positions and exits; contracts left open settle on Deriv's side, so a restart
between sessions strands nothing.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tools import lockfile  # noqa: E402
from tools.supervisor import child_python, day_pnl  # noqa: E402

# A crash loop is a bug, not a market condition: retrying it fast just fills
# the log. Backs off, and gives up rather than spinning forever.
BACKOFF_SECONDS = (5, 15, 60, 180)
MAX_CONSECUTIVE_FAILURES = 8


def log(msg: str, stream=None, path: str | None = None) -> None:
    """Log to `stream` and, if given, append to `path` as well.

    `path` matters more than it looks. Under pythonw - which is how the
    scheduled task runs this, deliberately, so no console event can kill it -
    there IS no stdout, so anything written only to the stream is discarded.
    That silently hid the most important message this program emits: the task
    fired, found the lock held, and exited cleanly with nothing recorded
    anywhere to say why.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{stamp}] {msg}"
    # Resolved at call time, never bound as a default: under pythonw there is
    # no stdout at import, and a default argument would capture None forever.
    out = stream if stream is not None else sys.stdout
    try:
        print(line, file=out, flush=True)
    except Exception:                    # noqa: BLE001 - no console under pythonw
        pass
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


# Windows console-control events are delivered to every process sharing a
# console, so a child launched normally dies with STATUS_CONTROL_C_EXIT
# (0xC000013A = 3221225786) when whatever started the supervisor goes away.
# That is exactly how the first five-symbol session died one second after
# opening. CREATE_NO_WINDOW gives the child no console to receive the event.
CREATE_NO_WINDOW = 0x08000000


def run_once(config: str, minutes: float, log_path: str) -> int:
    """One child session. Returns its exit code."""
    cmd = [child_python(), "-u", "run_pricebot.py", "--config", config,
           "--minutes", str(minutes)]
    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with open(log_path, "a", encoding="utf-8") as out:
        log(f"starting: {' '.join(cmd)}", out)
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                creationflags=flags)
        assert proc.stdout is not None
        for line in proc.stdout:
            out.write(line)
            out.flush()
        proc.wait()
        log(f"child exited with {proc.returncode}", out)
        return proc.returncode


# One shared lock implementation, in tools/lockfile.py. This module used to
# carry its own copy while tools/supervisor.py had none at all - which is
# precisely how two digit-bot supervisors ended up trading side by side. The
# names below are kept as thin aliases so existing callers and tests do not
# have to move.
#
# NAMED PER BOT, not hardcoded. This script now supervises more than one
# config (Rise/Fall, and this repo's NOTOUCH bot) - a lock hardcoded to
# "risefall_supervisor" would let two DIFFERENT bots, each convinced it holds
# the lock, run at once, which is exactly the failure this file exists to
# prevent. `--lock-name` defaults to the config's own basename so an existing
# `--config config.risefall.yaml` invocation with no other changes keeps its
# historical "risefall_supervisor" name.
DEFAULT_LOCK_NAME = "risefall_supervisor"


def lock_name_for(config_path: str) -> str:
    base = os.path.splitext(os.path.basename(config_path))[0]  # "config.notouch.yaml" -> "config.notouch"
    if base.startswith("config."):
        base = base[len("config."):]
    return DEFAULT_LOCK_NAME if base == "risefall" else base


def stale_lock(pid_text: str, pid_alive) -> bool:
    return lockfile.stale(pid_text, pid_alive)


def _pid_alive(pid: int) -> bool:
    return lockfile.pid_alive(pid)


def acquire_lock(lock_path: str) -> bool:
    return lockfile.acquire(lock_path)


def release_lock(lock_path: str) -> None:
    lockfile.release(lock_path)


def verdict(pnl: float, max_loss: float, target: float) -> str:
    """'trade' | 'loss-cap' | 'target' - the day's budget decision.

    Pure so the boundary behaviour is testable without a broker. Both limits
    are inclusive: hitting the cap exactly stops, because 'not worse than'
    is what a limit means.
    """
    # abs() on BOTH sides. Guarding on `max_loss > 0` would let a config
    # written as -100 - which reads perfectly naturally as "cap at minus a
    # hundred" - silently disable the cap entirely.
    if max_loss and pnl <= -abs(max_loss):
        return "loss-cap"
    if target and pnl >= abs(target):
        return "target"
    return "trade"


def seconds_until_next_utc_day(now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (tomorrow.timestamp() + 86400) - now.timestamp()


def main() -> None:
    ap = argparse.ArgumentParser(description="Supervise the Rise/Fall bot")
    ap.add_argument("--config", default="config.risefall.yaml")
    ap.add_argument("--journal", default="risefall_journal.csv")
    ap.add_argument("--log", default="risefall_live.log")
    ap.add_argument("--minutes", type=float, default=30.0,
                    help="length of each child session")
    ap.add_argument("--max-daily-loss", type=float, default=100.0,
                    help="stop for the day at this realised loss; 0 disables")
    ap.add_argument("--target-profit", type=float, default=0.0,
                    help="stop for the day at this realised profit; 0 disables")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--lock-name", default=None,
                    help="override the pid-lock name; defaults to one derived "
                         "from --config so different bots never share a lock")
    args = ap.parse_args()

    log_path = os.path.join(REPO, args.log)
    journal = os.path.join(REPO, args.journal)
    lock_path = lockfile.lock_path(REPO, args.lock_name or lock_name_for(args.config))
    failures = 0

    if not acquire_lock(lock_path):
        log("another supervisor already holds the lock - exiting rather than "
            "doubling the trade rate and the daily-loss cap", path=log_path)
        return

    log(f"supervisor up | config={args.config} cap={args.max_daily_loss} "
        f"target={args.target_profit or 'none'} session={args.minutes}m "
        f"lock={os.path.basename(lock_path)}",
        path=log_path)

    try:
        _loop(args, journal, log_path, failures)
    finally:
        release_lock(lock_path)


def _loop(args, journal: str, log_path: str, failures: int) -> None:
    while True:
        today = date.today() if False else datetime.now(timezone.utc).date()
        pnl = day_pnl(journal, today)
        state = verdict(pnl, args.max_daily_loss, args.target_profit)
        if state != "trade":
            wait = seconds_until_next_utc_day()
            log(f"{state}: day PnL {pnl:+.2f} - sleeping {wait/3600:.1f}h "
                f"until the next UTC day", path=log_path)
            if args.once:
                return
            time.sleep(min(wait, 3600))
            continue

        code = run_once(args.config, args.minutes, log_path)
        if args.once:
            return

        if code == 0:
            failures = 0
        else:
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                log(f"giving up after {failures} consecutive failures - "
                    f"this is a bug, not a market condition. See {args.log}.",
                    path=log_path)
                return
            delay = BACKOFF_SECONDS[min(failures - 1, len(BACKOFF_SECONDS) - 1)]
            log(f"failure {failures}, backing off {delay}s", path=log_path)
            time.sleep(delay)


if __name__ == "__main__":
    main()
