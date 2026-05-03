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
    if key_path.exists():
        return key_path.read_bytes()
    key = secrets.token_bytes(_KEY_SIZE)
    key_path.write_bytes(key)
    os.chmod(key_path, 0o600)
    return key


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
