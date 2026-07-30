"""One supervisor per bot, enforced by a pid file.

Extracted from risefall_supervisor so both supervisors share ONE
implementation. The digit bot did not have this and today showed exactly why:
its scheduled task sat in Queued, fired late, and started a second supervisor
alongside a manually started one. The two children ran different code - one
pre-fix, one post-fix - so a bot that had been corrected went on buying
contract types the config excluded, from a process nobody knew was there.

Two failure modes, and the lock has to get both right:

  a LIVE holder must block          two supervisors means double the trade rate
                                    and two independent daily-loss caps read
                                    from the same journal, so the cap you
                                    configured is silently doubled

  a DEAD holder must not block      otherwise one crash becomes a permanent
                                    outage, which is worse than the race

And a lock file that is simply missing is not permission to proceed blindly -
`ensure_held` re-asserts ownership before each child launch, because deleting
the file by hand disarms the guard without stopping anything.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable


def lock_path(repo: str, name: str) -> str:
    """One lock per bot name, so the two supervisors never collide."""
    return os.path.join(repo, f".{name}.lock")


def pid_alive(pid: int) -> bool:
    """Is this pid a running process? Unknown counts as gone."""
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, timeout=15)
            return str(pid) in (out.stdout or "")
        os.kill(pid, 0)
        return True
    except Exception:                    # noqa: BLE001 - unknown means gone
        return False


def stale(pid_text: str, alive: Callable[[int], bool] = pid_alive) -> bool:
    """Is a recorded pid safe to take over? Pure, so the rule is testable.

    Our own pid counts as stale: re-acquiring a lock we already hold must
    succeed, or `ensure_held` would refuse to continue partway through a run.
    """
    text = (pid_text or "").strip()
    if not text:
        return True
    try:
        pid = int(text)
    except ValueError:
        return True                      # unreadable lock is a stale lock
    if pid == os.getpid():
        return True
    return not alive(pid)


def read_holder(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def acquire(path: str, alive: Callable[[int], bool] = pid_alive) -> bool:
    """Claim ownership, or return False if a live process already holds it."""
    existing = read_holder(path) if os.path.exists(path) else ""
    if existing and not stale(existing, alive):
        return False
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        return False                     # cannot record it, so do not claim it
    return True


def ensure_held(path: str, alive: Callable[[int], bool] = pid_alive) -> bool:
    """Still ours? Re-take it if the file vanished; refuse if someone else has it.

    Called before every child launch rather than once at startup, because a
    lock removed by hand mid-run otherwise leaves the supervisor running
    unguarded - which is how two of them ended up trading at once.
    """
    holder = read_holder(path).strip()
    if holder == str(os.getpid()):
        return True
    return acquire(path, alive)


def release(path: str) -> None:
    """Release only if it is still ours - never delete another owner's lock."""
    try:
        if read_holder(path).strip() == str(os.getpid()):
            os.remove(path)
    except OSError:
        pass
