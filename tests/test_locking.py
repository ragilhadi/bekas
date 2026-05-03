"""Tests for single-instance process locking (P1.4)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from bekas.locking import AlreadyRunningError, ProcessLock, acquire_lock


def test_acquire_lock_creates_and_releases_lock():
    """Lock should be acquired and released cleanly."""
    lock_file = Path(tempfile.gettempdir()) / f"bekas_lock_test_{os.getpid()}.lock"
    lock_file.unlink(missing_ok=True)
    lock = acquire_lock(lock_file)
    try:
        assert lock_file.exists()
    finally:
        lock.release()
    assert not lock_file.exists()


def test_acquire_lock_fails_when_already_held():
    """Second acquire should raise AlreadyRunningError."""
    lock_file = Path(tempfile.gettempdir()) / f"bekas_lock_test_{os.getpid()}_dup.lock"
    lock_file.unlink(missing_ok=True)
    l1 = acquire_lock(lock_file)
    try:
        with pytest.raises(AlreadyRunningError):
            acquire_lock(lock_file)
    finally:
        l1.release()


def test_context_manager_releases_on_exit():
    """Context manager must release even on exception."""
    lock_file = Path(tempfile.gettempdir()) / f"bekas_lock_test_{os.getpid()}_ctx.lock"
    lock_file.unlink(missing_ok=True)
    try:
        with ProcessLock(lock_file):
            assert lock_file.exists()
    except Exception:
        pass
    if lock_file.exists():
        lock_file.unlink()
