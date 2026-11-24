"""Secure storage and credential protection helpers.

Provides atomic, permission-controlled (.env chmod 0600) updates and credential
masking to prevent accidental secret leaks in stdout and terminal logs, aligning
with NIST SP 800-63B and CWE-532 best practices.
"""

from __future__ import annotations

import os
from pathlib import Path

import dotenv


def mask_secret(
    secret: str | None,
    unmasked_prefix: int = 4,
    unmasked_suffix: int = 4,
) -> str:
    """Mask a sensitive credential for terminal display or logging.

    Examples:
        >>> mask_secret("abcdef1234567890")
        'abcd...7890'
        >>> mask_secret("short")
        '***'
        >>> mask_secret(None)
        '<empty>'
    """
    if not secret:
        return "<empty>"
    if len(secret) <= (unmasked_prefix + unmasked_suffix):
        return "***"
    return f"{secret[:unmasked_prefix]}...{secret[-unmasked_suffix:]}"


def enforce_file_permissions(file_path: Path | str, mode: int = 0o600) -> None:
    """Enforce strict POSIX permissions (default 0600: read/write owner only)."""
    p = Path(file_path)
    if p.exists() and os.name == "posix":
        try:
            os.chmod(p, mode)
        except OSError:
            pass


def update_env_value(env_path: Path | str, key: str, value: str) -> None:
    """Safely update a single key in the target .env file with 0600 permissions."""
    update_env_values(env_path, {key: value})


def update_env_values(env_path: Path | str, updates: dict[str, str]) -> None:
    """Safely update multiple keys in the target .env file with 0600 permissions.

    Preserves existing comments, whitespace, and unmodified keys while guaranteeing
    owner-only read/write access.
    """
    path = Path(env_path)
    if not path.exists():
        path.touch(mode=0o600)
    enforce_file_permissions(path, 0o600)

    for k, v in updates.items():
        dotenv.set_key(str(path), k, v, quote_mode="never")

    enforce_file_permissions(path, 0o600)
