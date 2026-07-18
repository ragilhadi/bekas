"""Branch coverage tests for locking.py — error paths and release edge cases."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from bekas.locking import AlreadyRunningError, ProcessLock, acquire_lock


def test_read_pid_returns_none_for_missing_file(tmp_path):
    """_read_pid on a nonexistent file is None."""
    lock = ProcessLock(tmp_path / "missing.lock")
    assert lock._read_pid() is None


def test_read_pid_returns_none_for_garbage(tmp_path):
    """_read_pid on non-integer content is None."""
    lock_file = tmp_path / "test.lock"
    lock_file.write_text("not-a-pid")
    assert ProcessLock(lock_file)._read_pid() is None


def test_read_pid_returns_none_for_nonpositive(tmp_path):
    """_read_pid treats PID <= 0 as invalid."""
    lock_file = tmp_path / "test.lock"
    lock_file.write_text("0")
    assert ProcessLock(lock_file)._read_pid() is None
    lock_file.write_text("-5")
    assert ProcessLock(lock_file)._read_pid() is None


def test_read_pid_returns_stored_pid(tmp_path):
    """_read_pid returns a valid positive PID."""
    lock_file = tmp_path / "test.lock"
    lock_file.write_text("12345")
    assert ProcessLock(lock_file)._read_pid() == 12345


def test_contention_message_with_unreadable_pid(tmp_path):
    """When the holder PID cannot be read, the error says 'PID unknown'."""
    lock_file = tmp_path / "test.lock"
    l1 = acquire_lock(lock_file)
    try:
        with (
            patch.object(ProcessLock, "_read_pid", return_value=None),
            pytest.raises(AlreadyRunningError) as excinfo,
        ):
            ProcessLock(lock_file).acquire()
        assert "PID unknown" in str(excinfo.value)
        assert excinfo.value.pid == 0
    finally:
        l1.release()


def test_contention_message_includes_pid_and_path(tmp_path):
    """On contention the message names the holder PID and the lock file."""
    lock_file = tmp_path / "test.lock"
    l1 = acquire_lock(lock_file)
    try:
        with pytest.raises(AlreadyRunningError) as excinfo:
            ProcessLock(lock_file).acquire()
        assert f"PID {os.getpid()}" in str(excinfo.value)
        assert str(lock_file) in str(excinfo.value)
        assert excinfo.value.pid == os.getpid()
    finally:
        l1.release()


def test_already_running_error_without_lock_file():
    """AlreadyRunningError is constructible without a lock path."""
    exc = AlreadyRunningError(42)
    assert exc.pid == 42
    assert "PID 42" in str(exc)
    assert "Lock file" not in str(exc)


def test_release_without_acquire(tmp_path):
    """Releasing a lock that was never acquired is safe."""
    lock = ProcessLock(tmp_path / "never.lock")
    lock.release()  # should not raise


def test_release_is_idempotent(tmp_path):
    """Calling release twice is safe."""
    lock = acquire_lock(tmp_path / "twice.lock")
    lock.release()
    lock.release()  # should not raise


def test_release_clears_pid_but_keeps_file(tmp_path):
    """release truncates the PID record instead of unlinking the file."""
    lock_file = tmp_path / "keep.lock"
    lock = acquire_lock(lock_file)
    lock.release()
    assert lock_file.exists()
    assert lock_file.read_text() == ""


@pytest.mark.skipif(os.name == "nt", reason="cannot unlink an open file on Windows")
def test_release_lock_file_gone(tmp_path):
    """Releasing when the lock file was unlinked externally is safe (POSIX)."""
    lock_file = tmp_path / "gone.lock"
    lock = acquire_lock(lock_file)
    lock_file.unlink()
    lock.release()  # should not raise


def test_acquire_creates_parent_directory(tmp_path):
    """acquire creates missing parent directories for the lock file."""
    lock_file = tmp_path / "deep" / "nested" / "bekas.lock"
    lock = acquire_lock(lock_file)
    try:
        assert lock_file.exists()
    finally:
        lock.release()


def test_acquire_does_not_truncate_holder_pid_on_contention(tmp_path):
    """A failed acquire must leave the holder's PID record intact."""
    lock_file = tmp_path / "test.lock"
    l1 = acquire_lock(lock_file)
    try:
        with pytest.raises(AlreadyRunningError):
            ProcessLock(lock_file).acquire()
        assert lock_file.read_text().strip() == str(os.getpid())
    finally:
        l1.release()
