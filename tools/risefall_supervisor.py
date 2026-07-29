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

from tools.supervisor import child_python, day_pnl  # noqa: E402

# A crash loop is a bug, not a market condition: retrying it fast just fills
# the log. Backs off, and gives up rather than spinning forever.
BACKOFF_SECONDS = (5, 15, 60, 180)
MAX_CONSECUTIVE_FAILURES = 8


def log(msg: str, stream=None) -> None:
    # Resolved at call time, never bound as a default: under pythonw there is
    # no stdout at import, and a default argument would capture None forever.
    out = stream if stream is not None else sys.stdout
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {msg}", file=out, flush=True)


def run_once(config: str, minutes: float, log_path: str) -> int:
    """One child session. Returns its exit code."""
    cmd = [child_python(), "-u", "run_pricebot.py", "--config", config,
           "--minutes", str(minutes)]
    with open(log_path, "a", encoding="utf-8") as out:
        log(f"starting: {' '.join(cmd)}", out)
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert proc.stdout is not None
        for line in proc.stdout:
            out.write(line)
            out.flush()
        proc.wait()
        log(f"child exited with {proc.returncode}", out)
        return proc.returncode


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
    args = ap.parse_args()

    log_path = os.path.join(REPO, args.log)
    journal = os.path.join(REPO, args.journal)
    failures = 0

    log(f"supervisor up | config={args.config} cap={args.max_daily_loss} "
        f"target={args.target_profit or 'none'} session={args.minutes}m")

    while True:
        today = date.today() if False else datetime.now(timezone.utc).date()
        pnl = day_pnl(journal, today)
        state = verdict(pnl, args.max_daily_loss, args.target_profit)
        if state != "trade":
            wait = seconds_until_next_utc_day()
            log(f"{state}: day PnL {pnl:+.2f} - sleeping {wait/3600:.1f}h "
                f"until the next UTC day")
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
                    f"this is a bug, not a market condition. See {args.log}.")
                return
            delay = BACKOFF_SECONDS[min(failures - 1, len(BACKOFF_SECONDS) - 1)]
            log(f"failure {failures}, backing off {delay}s")
            time.sleep(delay)


if __name__ == "__main__":
    main()
