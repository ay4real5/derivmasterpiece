"""Alert events, written for something else to display.

The supervisor runs as a scheduled task with an S4U principal, which means
session 0 — no desktop, so it cannot show a toast itself. (`notify.ps1`
already fails exactly this way: "Toast APIs unavailable (e.g.
non-interactive session)".) So the supervisor only *writes* alerts here, one
JSON object per line, and `alert_watcher.ps1` runs in the logged-in session,
tails this file, and does the displaying.

Append-only JSONL flushed per line, same discipline as the trade journal: a
tailing reader can follow it without locking, and a crash mid-write costs at
most the last line.

WHAT IS WORTH ALERTING ON is the real design question here. This connection
drops roughly every half hour and recovers by itself in about five seconds.
Alerting on every drop would be ~2 messages an hour that all say "it fixed
itself", which is how an alert channel gets ignored — and an ignored channel
is worse than none, because it looks like coverage. So drops are deliberately
silent, and only these are reported: a stall (nothing traded for minutes), a
crash loop (self-healing itself failing), a risk stop, and recovery afterwards.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


# Per-event-type cooldown. A machine that is genuinely down should produce one
# alert, not one per check for as long as it stays down.
DEFAULT_COOLDOWN = 900.0


@dataclass
class Alert:
    event: str
    level: str  # "problem" | "info"
    message: str
    ts: str = ""

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = datetime.now(timezone.utc).isoformat()


def should_emit(event: str, now: float, state: dict[str, float],
                cooldown: float = DEFAULT_COOLDOWN) -> bool:
    """True if `event` has not fired within `cooldown` seconds.

    `state` maps event -> last emit time (monotonic or epoch, caller's choice,
    just be consistent) and is mutated on success so the caller does not have
    to remember to.
    """
    last = state.get(event)
    if last is not None and (now - last) < cooldown:
        return False
    state[event] = now
    return True


def emit(path: str, alert: Alert, state: dict[str, float] | None = None,
         cooldown: float = DEFAULT_COOLDOWN, now: float | None = None) -> bool:
    """Append `alert` unless its event type is still in cooldown.

    Returns True if it was written. Never raises: an alerting system that can
    take down the supervisor it is meant to watch is worse than no alerting,
    so a failed write is swallowed (the log still has everything).
    """
    if state is not None:
        if not should_emit(alert.event, now if now is not None else time.time(),
                           state, cooldown):
            return False
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(alert)) + "\n")
            fh.flush()
        return True
    except OSError:
        return False


def read_alerts(path: str) -> list[dict[str, Any]]:
    """Every alert in the file, skipping any unparseable (e.g. half-written)
    line rather than failing the whole read.
    """
    if not os.path.exists(path):
        return []
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
