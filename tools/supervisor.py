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
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alerts import Alert, emit  # noqa: E402  (path set above so this runs as a script)

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


def last_trade_time(journal_path: str) -> datetime | None:
    """Timestamp of the newest settled trade, or None if there are none.

    Reads the last usable row rather than parsing the whole file every poll.
    """
    if not os.path.exists(journal_path):
        return None
    newest: datetime | None = None
    try:
        with open(journal_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                stamp = (row.get("timestamp") or "").strip()
                if not stamp or not (row.get("profit") or "").strip():
                    continue
                try:
                    newest = datetime.fromisoformat(stamp)
                except ValueError:
                    continue
    except OSError:
        return None
    return newest


def is_stalled(journal_path: str, stall_seconds: float,
               now: datetime | None = None,
               since: datetime | None = None) -> tuple[bool, float]:
    """(stalled, seconds of silence).

    Phase-independent on purpose. `MAX_EMPTY_SCANS` guards the scan loop and
    `SETTLEMENT_TIMEOUT` guards the post-buy wait, but a drop during the buy
    or balance call is covered by neither and hangs with the process alive.
    "Nothing has traded for N minutes" catches every such case without
    needing to know which call is stuck.

    `since` is when the current child started, and silence is measured from
    the LATER of that and the last trade. Without it the first version killed
    every freshly started child: after the bot had been idle overnight or
    parked for the day, the newest trade was already hours old, so the
    watchdog declared a stall within 30 seconds of launch and restarted it
    again, forever. A child that has only been alive 30s cannot have been
    silent for an hour.
    """
    now = now or datetime.now(timezone.utc)
    last = last_trade_time(journal_path)
    if last is None and since is None:
        return False, 0.0  # nothing traded yet; not evidence of a stall
    marks = [m for m in (last, since) if m is not None]
    age = (now - max(marks)).total_seconds()
    return age >= stall_seconds, age


class StallWatchdog:
    """Terminates a child that has stopped trading, so the normal restart
    path picks it up.

    Runs only while a child is alive. It must never fire while the supervisor
    is idling between UTC days after a risk stop — no trades are *expected*
    then, so their absence is not a stall.

    The threshold is deliberately generous. Legitimate gaps reach ~94s
    (measured after deep-review fetches were made concurrent; they reached
    223s before that), and a cycle is skipped whenever a category is not
    offered on the rotated symbol. Too tight and the watchdog becomes the
    outage it was meant to catch.
    """

    def __init__(self, journal_path: str, stall_seconds: float,
                 on_stall, poll_seconds: float = 30.0):
        self.journal_path = journal_path
        self.stall_seconds = stall_seconds
        self.on_stall = on_stall
        self.poll_seconds = poll_seconds
        self.child: Any = None  # set by run_once before start()
        self.started_at: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.fired = False

    def start(self) -> None:
        # Silence is measured from here, not from the last trade in the
        # journal, so a child launched after an idle period is not judged on
        # the previous session's silence.
        self.started_at = datetime.now(timezone.utc)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                stalled, age = is_stalled(self.journal_path, self.stall_seconds,
                                          since=self.started_at)
            except Exception:  # noqa: BLE001 — the watchdog may not crash the run
                continue
            if stalled:
                self.fired = True
                try:
                    self.on_stall(age)
                except Exception:  # noqa: BLE001
                    pass
                return


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


def run_once(config: str, log_path: str,
             watchdog: "StallWatchdog | None" = None) -> tuple[int, str | None]:
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

        # The watchdog only exists while this child does: between UTC days
        # after a risk stop there is no child and no expectation of trades,
        # so silence there must not read as a stall.
        if watchdog is not None:
            watchdog.child = proc
            watchdog.start()
        try:
            for line in proc.stdout:
                out.write(line)
                out.flush()
                if STOP_PREFIX in line:
                    stop_reason = line.split(STOP_PREFIX, 1)[1].strip()
            proc.wait()
        finally:
            if watchdog is not None:
                watchdog.stop()
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

    al = cfg.get("alerts", {}) or {}
    alerts_on = bool(al.get("enabled", False))
    alerts_path = os.path.join(REPO, str(al.get("file", "alerts.jsonl")))
    stall_seconds = float(al.get("stall_seconds", 300))
    cooldown = float(al.get("cooldown_seconds", 900))
    crash_loop_n = int(al.get("crash_loop_restarts", 3))
    crash_loop_window = float(al.get("crash_loop_window_seconds", 600))
    alert_state: dict[str, float] = {}
    restart_times: list[float] = []
    problem_open = False  # a stall/crash_loop was reported and not yet cleared

    def alert(event: str, level: str, message: str) -> None:
        """Write an alert for alert_watcher.ps1 to display. Never raises —
        this process must not be taken down by the thing watching it."""
        if not alerts_on:
            return
        if emit(alerts_path, Alert(event, level, message), alert_state, cooldown):
            log(f"ALERT [{level}] {event}: {message}")

    log(f"supervising scan-trade | max_daily_loss={max_daily_loss} "
        f"target_profit={target_profit} journal={journal} "
        f"alerts={'on' if alerts_on else 'off'} stall_seconds={stall_seconds:.0f}")

    backoff = MIN_BACKOFF
    while True:
        pnl = day_pnl(journal_path, utc_today())
        # on_target=continue must apply here too, not only to the child's stop
        # reason. It previously did not: the child was relaunched past its
        # target, but this day-level check then halted for the rest of the UTC
        # day anyway — so the two mechanisms disagreed and "keep running back
        # to back" silently stopped after 15 hours. The daily LOSS side is
        # never waived; that is the only real protection in the system.
        day_target = target_profit if on_target == "stop" else None
        verdict = budget_verdict(pnl, max_daily_loss, day_target)
        if verdict:
            wait = seconds_until_next_utc_day()
            log(f"NOT restarting — {verdict}. Sleeping {wait / 3600:.1f}h until the next UTC day.")
            # This halt used to be silent. It is the single most important
            # thing to report — the bot is deliberately done for the day, and
            # without an alert the user only finds out by noticing the balance
            # stopped moving, which is exactly what happened.
            alert("day_complete", "info",
                  f"{verdict} - idle until the next UTC day ({wait / 3600:.1f}h)")
            if args.once:
                return
            time.sleep(wait)
            continue

        log(f"today's realised PnL {pnl:+.2f} — within limits, launching")

        # Recovery is reported only if a problem was reported first, so a
        # normal restart stays silent.
        if problem_open and not is_stalled(journal_path, stall_seconds)[0]:
            alert_state.pop("recovered", None)
            alert("recovered", "info", f"trading again - balance PnL today {pnl:+.2f}")
            problem_open = False

        def _on_stall(age: float) -> None:
            nonlocal problem_open
            problem_open = True
            alert("stall", "problem",
                  f"no settled trade for {age / 60:.1f} min - restarting the bot")
            child = watchdog.child
            if child is not None and child.poll() is None:
                child.terminate()  # ends run_once's stdout loop -> normal restart

        watchdog = StallWatchdog(journal_path, stall_seconds, _on_stall) if alerts_on else None

        started = time.monotonic()
        stop_reason = None
        try:
            code, stop_reason = run_once(args.config, log_path, watchdog)
        except Exception as exc:  # noqa: BLE001 — a supervisor may not die
            code = -1
            log(f"launch failed: {exc!r}")
        ran_for = time.monotonic() - started

        # Self-healing failing repeatedly is worth interrupting someone for;
        # a single drop that recovers in 5s is not.
        now_mono = time.monotonic()
        restart_times.append(now_mono)
        restart_times[:] = [t for t in restart_times if now_mono - t <= crash_loop_window]
        if len(restart_times) >= crash_loop_n:
            problem_open = True
            alert("crash_loop", "problem",
                  f"{len(restart_times)} restarts in {crash_loop_window / 60:.0f} min "
                  f"- last exit {code}")

        if args.once:
            return

        # The child's stop reason outranks the journal check above. Without
        # this, a "profit target reached" stop was relaunched within seconds
        # (the day's journal PnL was still negative, so the budget check saw
        # nothing wrong) and the target silently meant nothing.
        kind = classify_stop(stop_reason)
        if kind in ("daily_loss", "target"):
            alert("risk_stop", "info", f"{stop_reason} (PnL today {pnl:+.2f})")
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
