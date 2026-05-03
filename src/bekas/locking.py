"""Single-instance lock for bekas to prevent concurrent mutations."""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any

from bekas.config import data_dir


class AlreadyRunningError(Exception):
    """Raised when another bekas process holds the lock."""

    def __init__(self, pid: int) -> None:
        """Initialize with the PID of the competing process.

        Args:
            pid: Process ID holding the lock.
        """
        super().__init__(f"Another bekas process is running (PID {pid}). Exiting.")
        self.pid = pid


class ProcessLock:
    """Cross-platform file-based process lock.

    Uses ``fcntl.flock`` on POSIX and ``msvcrt.locking`` on Windows.
    Stale locks (where the owning PID is dead) are reclaimed automatically.

    Attributes:
        lock_file: Path to the lock file.
        _fd: File descriptor of the opened lock file (POSIX).
        _handle: File handle (Windows).
    """

    def __init__(self, lock_file: Path | None = None) -> None:
        """Initialize the lock with an optional custom path.

        Args:
            lock_file: Custom lock file path. Defaults to ``<data_dir>/bekas.lock``.
        """
        self.lock_file = lock_file or (data_dir() / "bekas.lock")
        self._fd: Any = None
        self._handle: Any = None

    def _read_stale_pid(self) -> int | None:
        """Read the PID from the lock file if it exists and is valid.

        Returns:
            The stored PID, or None if the file does not exist, is unreadable,
            or contains an invalid PID (<= 0).
        """
        try:
            pid = int(self.lock_file.read_text().strip())
            if pid <= 0:
                return None
            return pid
        except Exception:
            return None

    def _is_pid_alive(self, pid: int) -> bool:
        """Check whether a process with the given PID is still running.

        Args:
            pid: Process ID to check.

        Returns:
            True if the process exists, False otherwise.
        """
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def acquire(self) -> None:
        """Acquire the lock, raising if another live process holds it.

        On contention, raises :class:`AlreadyRunningError` with exit code 3
        semantics. Stale locks from dead processes are silently reclaimed.

        Raises:
            AlreadyRunningError: If another live process holds the lock.
            OSError: If the lock file cannot be created or written.
        """
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        stale_pid = self._read_stale_pid()
        if stale_pid is not None and stale_pid != os.getpid() and self._is_pid_alive(stale_pid):
            raise AlreadyRunningError(stale_pid)

        if platform.system() == "Windows":
            self._acquire_windows()
        else:
            self._acquire_posix()

        # Write our PID into the lock file
        self.lock_file.write_text(str(os.getpid()))

    def _acquire_posix(self) -> None:
        """Acquire an exclusive lock via ``fcntl.flock``.

        Raises:
            AlreadyRunningError: If another process holds the lock.
            OSError: On lock acquisition failure.
        """
        import fcntl

        fd = open(self.lock_file, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            fd.close()
            raise AlreadyRunningError(0) from None
        self._fd = fd

    def _acquire_windows(self) -> None:
        """Acquire an exclusive lock via ``msvcrt.locking``.

        Raises:
            AlreadyRunningError: If another process holds the lock.
            OSError: On lock acquisition failure.
        """
        import msvcrt

        handle = open(self.lock_file, "w")
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            raise AlreadyRunningError(0) from None
        self._handle = handle

    def release(self) -> None:
        """Release the lock and clean up the lock file.

        Safe to call even if the lock was never acquired.
        """
        try:
            if platform.system() == "Windows":
                if self._handle is not None:
                    import msvcrt

                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                    self._handle.close()
                    self._handle = None
            else:
                if self._fd is not None:
                    import fcntl

                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                    self._fd.close()
                    self._fd = None
        finally:
            try:
                if self.lock_file.exists():
                    self.lock_file.unlink()
            except OSError:
                pass

    def __enter__(self) -> ProcessLock:
        """Context manager entry: acquire the lock.

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
