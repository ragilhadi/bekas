"""Single-instance lock for bekas to prevent concurrent mutations."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from bekas.config import data_dir

# Windows: lock a single byte well past any PID data so that other handles
# (including other processes reading the holder's PID) are never blocked by
# the mandatory byte-range lock.
_LOCK_REGION_OFFSET = 4096


class AlreadyRunningError(Exception):
    """Raised when another bekas process holds the lock."""

    def __init__(self, pid: int | None, lock_file: Path | None = None) -> None:
        """Initialize with the PID of the competing process.

        Args:
            pid: Process ID holding the lock, or None if it could not be read.
            lock_file: Path to the contended lock file, if known.
        """
        who = f"PID {pid}" if pid else "PID unknown"
        msg = f"Another bekas process is running ({who}). Exiting."
        if lock_file is not None:
            msg += f" Lock file: {lock_file}"
        super().__init__(msg)
        self.pid = pid or 0


class ProcessLock:
    """Cross-platform file-based process lock.

    Uses ``fcntl.flock`` on POSIX and ``msvcrt.locking`` on Windows. The
    kernel lock is the single source of truth: it is released automatically
    when the owning process exits for any reason (including SIGKILL), so a
    leftover lock file never blocks a new process. The PID written into the
    file is informational only, used for error messages on contention.

    Attributes:
        lock_file: Path to the lock file.
        _fd: Open file handle holding the lock, or None when not held.
    """

    def __init__(self, lock_file: Path | None = None) -> None:
        """Initialize the lock with an optional custom path.

        Args:
            lock_file: Custom lock file path. Defaults to ``<data_dir>/bekas.lock``.
        """
        self.lock_file = lock_file or (data_dir() / "bekas.lock")
        self._fd: Any = None

    def _read_pid(self) -> int | None:
        """Best-effort read of the holder's PID for error messages.

        Returns:
            The stored PID, or None if the file is missing, unreadable, or
            does not contain a positive integer.
        """
        try:
            pid = int(self.lock_file.read_text().strip())
            return pid if pid > 0 else None
        except Exception:
            return None

    def acquire(self) -> None:
        """Acquire the lock, raising if another live process holds it.

        Idempotent: calling acquire on a lock this instance already holds is
        a no-op. Lock files left behind by dead processes (crash, SIGKILL)
        carry no kernel lock and are reclaimed transparently.

        Raises:
            AlreadyRunningError: If another live process holds the lock.
            OSError: If the lock file cannot be created or written.
        """
        if self._fd is not None:
            return

        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        # "a+" so an existing holder's PID file is never truncated before we
        # actually own the kernel lock.
        fd = open(self.lock_file, "a+")
        try:
            if sys.platform == "win32":
                import msvcrt

                fd.seek(_LOCK_REGION_OFFSET)
                msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fd.close()
            raise AlreadyRunningError(self._read_pid(), self.lock_file) from None

        self._fd = fd
        # Record our PID through the same handle (a second handle would hit
        # the mandatory byte-range lock on Windows).
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()))
        fd.flush()

    def release(self) -> None:
        """Release the lock and clear the recorded PID.

        Safe to call even if the lock was never acquired. The lock file is
        truncated rather than deleted: unlinking would allow a new process to
        recreate the file and lock a different inode while a third process
        still races on the old one.
        """
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            fd.seek(0)
            fd.truncate()
            fd.flush()
            if sys.platform == "win32":
                import msvcrt

                fd.seek(_LOCK_REGION_OFFSET)
                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            fd.close()

    def __enter__(self) -> ProcessLock:
        """Context manager entry: acquire the lock (no-op if already held).

        Returns:
            Self for use in ``with`` statements.
        """
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        """Context manager exit: release the lock."""
        self.release()


def acquire_lock(lock_file: Path | None = None) -> ProcessLock:
    """Convenience function to acquire and return a :class:`ProcessLock`.

    Args:
        lock_file: Optional custom lock file path.

    Returns:
        An acquired :class:`ProcessLock` instance.

    Raises:
        AlreadyRunningError: If another live process holds the lock.
    """
    lock = ProcessLock(lock_file)
    lock.acquire()
    return lock
