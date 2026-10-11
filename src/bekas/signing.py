"""HMAC signing for saved plans.

Uses a machine-local secret stored in the bekas data directory.
This ensures plans can only be loaded on the same machine they were saved on,
preventing tampered plan files from being executed on another system.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from bekas.config import data_dir

_KEY_FILE_NAME = "plan_signing.key"
_KEY_SIZE = 32


def _get_or_create_key() -> bytes:
    """Load or create the machine-local signing key.

    Returns:
        32-byte signing key.
    """
    key_path = data_dir() / _KEY_FILE_NAME
    try:
        return key_path.read_bytes()
    except FileNotFoundError:
        pass
    key = secrets.token_bytes(_KEY_SIZE)
    # Create the file with owner-only permissions from the start (no window in
    # which it is readable under the default umask). O_EXCL makes two racing
    # processes agree on a single key: the loser reads the winner's.
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read_key_when_ready(key_path)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


def _read_key_when_ready(key_path: Path) -> bytes:
    """Read a key another process is in the middle of creating.

    Args:
        key_path: Path of the key file.

    Returns:
        The full key once the writer has finished.

    Raises:
        OSError: If the key never becomes complete.
    """
    for _ in range(50):
        data = key_path.read_bytes()
        if len(data) == _KEY_SIZE:
            return data
        time.sleep(0.02)
    raise OSError(f"signing key {key_path} is incomplete")


def sign_plan(plan_data: dict[str, Any]) -> str:
    """Return an HMAC hex signature for the given plan data.

    Args:
        plan_data: Dictionary representing the plan.

    Returns:
        Hex-encoded HMAC-SHA256 signature.
    """
    key = _get_or_create_key()
    payload = json.dumps(plan_data, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_plan(plan_data: dict[str, Any], signature: str) -> bool:
    """Verify the HMAC signature of a plan.

    Args:
        plan_data: Dictionary representing the plan.
        signature: Expected hex-encoded HMAC signature.

    Returns:
        True if the signature is valid, False otherwise.
    """
    try:
        expected = sign_plan(plan_data)
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False
