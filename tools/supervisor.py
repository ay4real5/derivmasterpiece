"""Keeps `scan-trade` running across crashes, network drops and reboots —
without quietly defeating the risk limits.

The bot exits whenever `RiskManager` trips a limit, and `risk.py` is explicit
that "nothing here auto-resumes, so a fresh process/day has to be started
deliberately". A naive restart loop would break that: `daily_pnl` starts at
0.0 in every new process, so relaunching after a -1000 stop would hand the
bot another -1000 to lose, and so on until the account is gone. That is the
single most dangerous thing a supervisor could do here.

So this supervisor reconstructs the day's realised PnL from
`trade_journal.csv` (which is flushed per trade and therefore survives any
restart) and refuses to relaunch while today's PnL is already past
`max_daily_loss` or `target_profit`. It waits for the next UTC day instead —
the same boundary `RiskManager._roll_day_if_needed` uses.

What it DOES restart, immediately: crashes, dropped websockets, anything
that kills the process while the day's budget is still intact. Backoff is
exponential so a hard-down API doesn't spin.

Note what this cannot change: restarting a negative-expectancy strategy more
reliably makes the expected loss arrive more reliably. This keeps the bot
running as instructed; it does not make it profitable. See
`tools/martingale_sim.py`.
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MIN_BACKOFF = 5.0
MAX_BACKOFF = 300.0
# A run that lasted this long counts as "healthy" — the next crash starts
# backing off from scratch rather than inheriting the previous streak.
HEALTHY_RUN_SECONDS = 120.0


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def day_pnl(journal_path: str, day: date) -> float:
    """Realised PnL booked on `day` (UTC), read back from the journal.

    Missing file, header row, dry-run rows with a blank profit and any
    half-written final line are all skipped rather than raising — the
    supervisor must never die because the journal looked odd.
    """
    if not os.path.exists(journal_path):
        return 0.0
    prefix = day.isoformat()
    total = 0.0
    with open(journal_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            stamp = (row.get("timestamp") or "").strip()
            if not stamp.startswith(prefix):
                continue
            raw = (row.get("profit") or "").strip()
            if not raw:
                continue  # dry-run / unsettled row
            try:
                total += float(raw)
            except ValueError:
                continue
    return total


def seconds_until_next_utc_day(now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    tomorrow = now.date() + timedelta(days=1)
    midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)
    return max(1.0, (midnight - now).total_seconds())


def budget_verdict(pnl: float, max_daily_loss: float,
                   target_profit: float | None) -> str | None:
    """Why today is over, or None if the bot may still trade.

    Mirrors `RiskManager._check_limits` for the two limits that persist
    across a restart. Consecutive-loss and trade-count limits are per
    process and deliberately not reconstructed — a fresh process legitimately
    starts those at zero.
    """
    if target_profit is not None and pnl >= target_profit:
        return f"profit target already reached today ({pnl:+.2f} >= {target_profit:+.2f})"
    if pnl <= -abs(max_daily_loss):
        return f"daily loss limit already reached today ({pnl:.2f} <= {-abs(max_daily_loss):.2f})"
    return None


STOP_PREFIX = "Risk manager stopped the bot: "


def child_python() -> str:
    """Interpreter to launch the bot with.

    The supervisor itself is run by pythonw.exe (see install_task.ps1) so
    that it owns no console and cannot be killed by a console-close event.
    The child doesn't need that — its output goes down a pipe either way —
    and console python keeps stack traces intact, so swap pythonw back to
    python when handing off.
    """
    exe = sys.executable
    base = os.path.basename(exe).lower()
    if base.startswith("pythonw"):
        candidate = os.path.join(os.path.dirname(exe), base.replace("pythonw", "python", 1))
        if os.path.exists(candidate):
            return candidate
    return exe


def attach_stdio_to_log(log_path: str) -> None:
    """pythonw.exe gives the process no stdout/stderr at all — they are None,
    and the first `print` raises AttributeError. Point them at the log file
    so the supervisor's own messages (and any traceback) still land
    somewhere readable.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    handle = open(log_path, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle


def classify_stop(reason: str | None) -> str | None:
    """Bucket a `RiskManager.stop_reason` into how the supervisor should react.

    "target"/"daily_loss" are day-scoped decisions and must survive a
    restart. The other two limits are per-process by design — a fresh
    process legitimately starts its consecutive-loss and trade counters at
    zero — so they are transient and may be restarted immediately.
    """
    if not reason:
        return None
    if "profit target reached" in reason:
        return "target"
    if "max daily loss reached" in reason:
        return "daily_loss"
    if "consecutive losses" in reason or "max trade count" in reason:
        return "transient"
    return None


def log(line: str, stream=None) -> None:
    # Resolved at call time, never as a default argument: under pythonw.exe
    # sys.stdout is None at import, so `stream=sys.stdout` would bind None
    # forever and every log() call would raise AttributeError — even after
    # attach_stdio_to_log() had repaired sys.stdout.
    stream = stream if stream is not None else sys.stdout
    if stream is None:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stream.write(f"[supervisor {stamp}] {line}\n")
    stream.flush()


def run_once(config: str, log_path: str) -> tuple[int, str | None]:
    """Launch one scan-trade process, streaming its output into `log_path`.

    Returns (exit code, RiskManager stop reason if it printed one). The
    child's own stop reason is the authority on WHY it ended — the journal
    can't distinguish "hit the profit target" from "still mid-session",
    because RiskManager measures PnL from process start while the journal
    measures the whole UTC day.

    `-u` matters: without it Python block-buffers stdout when it is a file,
    so a live session can run for many minutes showing nothing at all.
    """
    cmd = [child_python(), "-u", "main.py", "scan-trade", "--config", config]
    stop_reason: str | None = None
    with open(log_path, "a", encoding="utf-8") as out:
        log(f"starting: {' '.join(cmd)}", out)
        proc = subprocess.Popen(
            cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            out.write(line)
            out.flush()
            if STOP_PREFIX in line:
                stop_reason = line.split(STOP_PREFIX, 1)[1].strip()
        proc.wait()
        log(f"exited with code {proc.returncode}"
            + (f" — {stop_reason}" if stop_reason else ""), out)
        return proc.returncode, stop_reason


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--log", default="scan_trade_live.log")
    ap.add_argument("--journal", default=None, help="defaults to the config's journal_path")
    ap.add_argument("--once", action="store_true", help="run a single launch and exit (for testing)")
    ap.add_argument(
        "--on-target", choices=["stop", "continue"], default=None,
        help="what to do when the bot reports its profit target reached. "
             "'stop' banks the win and waits for the next UTC day. 'continue' "
             "relaunches immediately, which means the target never banks "
             "anything and the -max_daily_loss stop becomes the only terminal "
             "state. Overrides supervisor.on_target in the config; defaults "
             "to 'stop' if neither is set.",
    )
    args = ap.parse_args()

    import yaml  # local import so --help works without deps installed

    with open(os.path.join(REPO, args.config), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    risk = cfg.get("risk", {})
    max_daily_loss = float(risk.get("max_daily_loss", 0) or 0)
    target_profit = risk.get("target_profit")
    target_profit = float(target_profit) if target_profit is not None else None
    # Config-driven so this can change without re-registering the scheduled
    # task — re-registering unelevated silently drops the S4U principal back
    # to Interactive and loses logoff survival.
    on_target = args.on_target or str(cfg.get("supervisor", {}).get("on_target", "stop")).lower()
    if on_target not in ("stop", "continue"):
        raise SystemExit(f"supervisor.on_target must be 'stop' or 'continue', got {on_target!r}")
    journal = args.journal or cfg.get("journal_path", "trade_journal.csv")
    journal_path = os.path.join(REPO, journal)
    log_path = os.path.join(REPO, args.log)
    attach_stdio_to_log(log_path)

    log(f"supervising scan-trade | max_daily_loss={max_daily_loss} "
        f"target_profit={target_profit} journal={journal}")

    backoff = MIN_BACKOFF
    while True:
        pnl = day_pnl(journal_path, utc_today())
        verdict = budget_verdict(pnl, max_daily_loss, target_profit)
        if verdict:
            wait = seconds_until_next_utc_day()
            log(f"NOT restarting — {verdict}. Sleeping {wait / 3600:.1f}h until the next UTC day.")
            if args.once:
                return
            time.sleep(wait)
            continue

        log(f"today's realised PnL {pnl:+.2f} — within limits, launching")
        started = time.monotonic()
        stop_reason = None
        try:
            code, stop_reason = run_once(args.config, log_path)
        except Exception as exc:  # noqa: BLE001 — a supervisor may not die
            code = -1
            log(f"launch failed: {exc!r}")
        ran_for = time.monotonic() - started

        if args.once:
            return

        # The child's stop reason outranks the journal check above. Without
        # this, a "profit target reached" stop was relaunched within seconds
        # (the day's journal PnL was still negative, so the budget check saw
        # nothing wrong) and the target silently meant nothing.
        kind = classify_stop(stop_reason)
        if kind == "daily_loss" or (kind == "target" and on_target == "stop"):
            wait = seconds_until_next_utc_day()
            log(f"bot stopped deliberately — {stop_reason}. "
                f"Not restarting; sleeping {wait / 3600:.1f}h until the next UTC day.")
            time.sleep(wait)
            backoff = MIN_BACKOFF
            continue
        if kind == "target":
            log(f"profit target reached ({stop_reason}) but on_target=continue "
                f"— relaunching back-to-back. The target banks nothing in this mode; "
                f"-{max_daily_loss:.0f} is the only state that ends the day.")

        if ran_for >= HEALTHY_RUN_SECONDS:
            backoff = MIN_BACKOFF
        log(f"ran {ran_for:.0f}s, exit={code}; restarting in {backoff:.0f}s")
        time.sleep(backoff)
        backoff = min(MAX_BACKOFF, backoff * 2)


if __name__ == "__main__":
    main()
