"""Branch coverage tests for locking.py — stale PIDs, Windows, release edge cases."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from bekas.locking import AlreadyRunningError, ProcessLock, acquire_lock


def test_process_lock_stale_pid_reclaimed(tmp_path):
    """A lock file with a dead PID should be reclaimed."""
    lock_file = tmp_path / "test.lock"
    lock_file.write_text("999999")  # Non-existent PID
    lock = acquire_lock(lock_file)
    try:
        assert lock_file.exists()
        assert lock_file.read_text().strip() == str(os.getpid())
    finally:
        lock.release()
    assert not lock_file.exists()


def test_process_lock_own_pid_ok(tmp_path):
    """Lock file with our own PID is fine (reentrant-ish)."""
    lock_file = tmp_path / "test.lock"
    lock_file.write_text(str(os.getpid()))
    lock = acquire_lock(lock_file)
    try:
        assert lock_file.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_process_lock_invalid_pid_file(tmp_path):
    """Lock file with garbage content is treated as stale."""
    lock_file = tmp_path / "test.lock"
    lock_file.write_text("not-a-pid")
    lock = acquire_lock(lock_file)
    try:
        assert lock_file.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_process_lock_zero_pid(tmp_path):
    """Lock file with PID 0 is treated as invalid/stale."""
    lock_file = tmp_path / "test.lock"
    lock_file.write_text("0")
    lock = acquire_lock(lock_file)
    try:
        assert lock_file.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_process_lock_read_exception(tmp_path):
    """Exception reading lock file is treated as no stale PID."""
    lock_file = tmp_path / "test.lock"
    lock_file.write_text("1")
    with patch.object(Path, "read_text", side_effect=OSError("boom")):
        lock = acquire_lock(lock_file)
        try:
            assert lock_file.exists()
        finally:
            lock.release()


def test_acquire_posix_blocking_error(tmp_path):
    """POSIX flock blocking raises AlreadyRunningError."""
    lock_file = tmp_path / "test.lock"
    with (
        patch("bekas.locking.platform.system", return_value="Linux"),
        patch("fcntl.flock", side_effect=BlockingIOError),
        pytest.raises(AlreadyRunningError),
    ):
        ProcessLock(lock_file).acquire()


def test_acquire_posix_oserror(tmp_path):
    """POSIX flock OSError raises AlreadyRunningError."""
    lock_file = tmp_path / "test.lock"
    with (
        patch("bekas.locking.platform.system", return_value="Linux"),
        patch("fcntl.flock", side_effect=OSError(11, "Resource temporarily unavailable")),
        pytest.raises(AlreadyRunningError),
    ):
        ProcessLock(lock_file).acquire()


def test_acquire_windows_oserror(tmp_path):
    """Windows msvcrt.locking OSError raises AlreadyRunningError."""
    lock_file = tmp_path / "test.lock"
    fake_msvcrt = type("M", (), {"LK_NBLCK": 1, "locking": staticmethod(lambda *a, **k: (_ for _ in ()).throw(OSError("locked")))})()
    with (
        patch("bekas.locking.platform.system", return_value="Windows"),
        patch.dict("sys.modules", {"msvcrt": fake_msvcrt}),
        pytest.raises(AlreadyRunningError),
    ):
        ProcessLock(lock_file).acquire()


def test_release_without_acquire(tmp_path):
    """Releasing a lock that was never acquired is safe."""
    lock = ProcessLock(tmp_path / "never.lock")
    lock.release()  # should not raise


def test_release_lock_file_gone(tmp_path):
    """Releasing when the lock file is already gone is safe."""
    lock_file = tmp_path / "gone.lock"
    lock = acquire_lock(lock_file)
    lock_file.unlink()
    lock.release()  # should not raise
    assert not lock_file.exists()


def test_context_manager_exception_releases(tmp_path):
    """Context manager releases even on exception."""
    lock_file = tmp_path / "ctx.lock"
    try:
        with ProcessLock(lock_file):
            assert lock_file.exists()
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    # Lock file should be cleaned up despite exception
    assert not lock_file.exists()
