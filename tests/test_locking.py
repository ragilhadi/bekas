"""Behavior tests for the single-instance process lock.

These run the real lock implementation (no mocks) on every OS, including
cross-process contention and crash recovery via subprocesses.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from bekas.locking import AlreadyRunningError, ProcessLock, acquire_lock

# Subprocess that acquires the lock, signals readiness, then holds it. The
# lock must stay referenced: dropping it would close the fd and release the
# kernel lock. It prints its own PID because sys.executable can be a launcher
# (Windows venv), making Popen.pid differ from the interpreter's PID.
_HOLDER_CODE = """
import os, sys, time
from pathlib import Path
from bekas.locking import acquire_lock

lock = acquire_lock(Path(sys.argv[1]))
print(f"HELD {os.getpid()}", flush=True)
time.sleep(30)
lock.release()
"""


def _spawn_holder(lock_file):
    """Start a subprocess that acquires ``lock_file`` and holds it.

    Returns:
        Tuple of (Popen object, PID of the interpreter holding the lock).
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_CODE, str(lock_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    line = proc.stdout.readline().strip()
    if not line.startswith("HELD "):
        proc.kill()
        _, err = proc.communicate()
        raise RuntimeError(f"holder subprocess failed to acquire the lock: {err}")
    return proc, int(line.split()[1])


def _acquire_with_retry(lock_file, attempts=50, delay=0.1):
    """Acquire with brief retries (lock release after kill can lag slightly)."""
    for _ in range(attempts - 1):
        try:
            return acquire_lock(lock_file)
        except AlreadyRunningError:
            time.sleep(delay)
    return acquire_lock(lock_file)


def test_acquire_lock_creates_and_releases_lock(tmp_path):
    """Lock should be acquired and released cleanly, recording our PID."""
    lock_file = tmp_path / "bekas.lock"
    lock = acquire_lock(lock_file)
    try:
        assert lock_file.exists()
        assert lock_file.read_text().strip() == str(os.getpid())
    finally:
        lock.release()
    # After release the lock must be re-acquirable and the PID cleared.
    assert lock_file.read_text() == ""
    relock = acquire_lock(lock_file)
    relock.release()


def test_acquire_fails_when_other_process_holds_lock(tmp_path):
    """Contention with a real second process raises with its actual PID."""
    lock_file = tmp_path / "bekas.lock"
    holder, holder_pid = _spawn_holder(lock_file)
    try:
        with pytest.raises(AlreadyRunningError) as excinfo:
            acquire_lock(lock_file)
        assert excinfo.value.pid == holder_pid
        assert str(holder_pid) in str(excinfo.value)
        assert str(lock_file) in str(excinfo.value)
    finally:
        holder.kill()
        holder.wait()


def test_lock_reclaimed_after_holder_killed(tmp_path):
    """Regression: a SIGKILL'd holder must never leave a stuck lock behind."""
    lock_file = tmp_path / "bekas.lock"
    holder, _holder_pid = _spawn_holder(lock_file)
    holder.kill()  # SIGKILL / TerminateProcess: no cleanup code runs
    holder.wait()

    lock = _acquire_with_retry(lock_file)
    try:
        assert lock_file.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


@pytest.mark.parametrize("content", ["999999", "not-a-pid", "0", ""])
def test_stale_lock_file_content_is_ignored(tmp_path, content):
    """Leftover lock files carry no kernel lock and must be reclaimed."""
    lock_file = tmp_path / "bekas.lock"
    lock_file.write_text(content)
    lock = acquire_lock(lock_file)
    try:
        assert lock_file.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_acquire_is_idempotent_for_same_instance(tmp_path):
    """Regression for the cli pattern: acquire_lock() followed by `with lock:`."""
    lock_file = tmp_path / "bekas.lock"
    lock = acquire_lock(lock_file)
    with lock:
        pass  # __enter__ must not re-acquire and self-conflict
    # __exit__ released; a fresh instance can take the lock.
    relock = acquire_lock(lock_file)
    relock.release()


def test_second_instance_in_same_process_conflicts(tmp_path):
    """Two ProcessLock instances conflict even within one process."""
    lock_file = tmp_path / "bekas.lock"
    l1 = acquire_lock(lock_file)
    try:
        with pytest.raises(AlreadyRunningError):
            ProcessLock(lock_file).acquire()
    finally:
        l1.release()


def test_context_manager_releases_on_exception(tmp_path):
    """Context manager must release even when the body raises."""
    lock_file = tmp_path / "bekas.lock"
    with pytest.raises(RuntimeError), ProcessLock(lock_file):
        raise RuntimeError("boom")
    relock = acquire_lock(lock_file)
    relock.release()
