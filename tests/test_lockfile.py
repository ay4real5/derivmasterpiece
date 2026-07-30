"""One lock implementation, shared. Both supervisors depend on it being right.

The digit bot had NO lock, and its scheduled task - stuck in Queued - fired
late and ran alongside a manually started supervisor. Two children on
different code, and the daily-loss cap silently doubled because each read the
same journal and applied its own limit.
"""
import os

import pytest

from tools import lockfile


def test_a_live_holder_blocks(tmp_path):
    p = str(tmp_path / "a.lock")
    with open(p, "w") as fh:
        fh.write("4321")
    assert lockfile.acquire(p, alive=lambda pid: True) is False


def test_a_dead_holder_is_taken_over(tmp_path):
    """A crash must not become a permanent outage."""
    p = str(tmp_path / "a.lock")
    with open(p, "w") as fh:
        fh.write("4321")
    assert lockfile.acquire(p, alive=lambda pid: False) is True
    assert open(p).read().strip() == str(os.getpid())


def test_an_unreadable_lock_is_stale():
    for junk in ("", "   ", "not-a-pid", "12.5", None):
        assert lockfile.stale(junk, lambda pid: True) is True


def test_our_own_pid_never_blocks_us():
    assert lockfile.stale(str(os.getpid()), lambda pid: True) is True


def test_acquiring_a_free_lock_records_our_pid(tmp_path):
    p = str(tmp_path / "a.lock")
    assert lockfile.acquire(p) is True
    assert open(p).read().strip() == str(os.getpid())


def test_ensure_held_retakes_a_deleted_lock(tmp_path):
    """Deleting the file by hand disarms the guard without stopping anything,
    so every launch re-asserts rather than trusting startup."""
    p = str(tmp_path / "a.lock")
    assert lockfile.acquire(p) is True
    os.remove(p)
    assert lockfile.ensure_held(p) is True
    assert open(p).read().strip() == str(os.getpid())


def test_ensure_held_refuses_when_someone_else_took_over(tmp_path):
    p = str(tmp_path / "a.lock")
    lockfile.acquire(p)
    with open(p, "w") as fh:
        fh.write("4321")
    assert lockfile.ensure_held(p, alive=lambda pid: True) is False


def test_release_only_removes_our_own_lock(tmp_path):
    p = str(tmp_path / "a.lock")
    with open(p, "w") as fh:
        fh.write("4321")
    lockfile.release(p)
    assert os.path.exists(p), "released a lock belonging to another process"


def test_release_removes_ours(tmp_path):
    p = str(tmp_path / "a.lock")
    lockfile.acquire(p)
    lockfile.release(p)
    assert not os.path.exists(p)


def test_the_two_bots_use_different_lock_files():
    """A shared lock file would make one bot block the other."""
    a = lockfile.lock_path("/repo", "scan_trade_supervisor")
    b = lockfile.lock_path("/repo", "risefall_supervisor")
    assert a != b


def test_pid_alive_finds_this_process_and_rejects_nonsense():
    assert lockfile.pid_alive(os.getpid()) is True
    assert lockfile.pid_alive(0) is False
    assert lockfile.pid_alive(-5) is False


def test_both_supervisors_actually_acquire_a_lock():
    """Guards against the lock existing but never being called - which was the
    digit supervisor's exact state."""
    for mod in ("tools/supervisor.py", "tools/risefall_supervisor.py"):
        src = open(mod, encoding="utf-8").read()
        assert "acquire" in src, f"{mod} never acquires a lock"


# --- the supervisors must actually RUN, not merely import under pytest ------

@pytest.mark.parametrize("script", ["tools/supervisor.py",
                                    "tools/risefall_supervisor.py"])
def test_supervisor_runs_as_a_script(script):
    """Import errors are invisible to pytest, which puts the repo root on
    sys.path itself. The scheduled task does not - it runs the file directly,
    so a `from tools.x import y` that works in tests dies in production with
    ModuleNotFoundError and the task just reports exit 1.

    That happened: the digit supervisor was given a `from tools.lockfile`
    import while the file only ever puts tools/ on sys.path.
    """
    import subprocess
    import sys
    out = subprocess.run([sys.executable, script, "--help"],
                         capture_output=True, text=True, timeout=90)
    assert "ModuleNotFoundError" not in out.stderr, out.stderr[-400:]
    assert out.returncode == 0, out.stderr[-400:]
