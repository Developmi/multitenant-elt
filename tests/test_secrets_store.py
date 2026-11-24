import os
from pathlib import Path

from agency_analytics.secrets_store import (
    enforce_file_permissions,
    mask_secret,
    update_env_value,
    update_env_values,
)


def test_mask_secret_standard():
    assert mask_secret("abcdef1234567890") == "abcd...7890"
    assert (
        mask_secret("EAABwzLIXf1234567890xyz", unmasked_prefix=6, unmasked_suffix=3)
        == "EAABwz...xyz"
    )


def test_mask_secret_edge_cases():
    assert mask_secret(None) == "<empty>"
    assert mask_secret("") == "<empty>"
    assert mask_secret("short") == "***"
    assert mask_secret("12345678") == "***"  # Exactly prefix + suffix (4+4)


def test_enforce_file_permissions(tmp_path: Path):
    test_file = tmp_path / ".env.test"
    test_file.write_text("FOO=BAR\n")

    enforce_file_permissions(test_file, 0o600)
    if os.name == "posix":
        mode = test_file.stat().st_mode & 0o777
        assert mode == 0o600

    # Verify OSError handling (e.g. read-only filesystem)
    from unittest.mock import patch

    with patch("os.chmod", side_effect=OSError("Read-only filesystem")):
        # Should not raise
        enforce_file_permissions(test_file, 0o600)


def test_update_env_value_and_permissions(tmp_path: Path):
    target_env = tmp_path / ".env"

    update_env_value(target_env, "TEST_API_KEY", "super_secret_123")
    assert target_env.exists()
    content = target_env.read_text()
    assert "TEST_API_KEY=super_secret_123" in content

    if os.name == "posix":
        assert (target_env.stat().st_mode & 0o777) == 0o600

    # Update again to verify idempotency and value replacement
    update_env_value(target_env, "TEST_API_KEY", "super_secret_456")
    content = target_env.read_text()
    assert "TEST_API_KEY=super_secret_456" in content
    assert "super_secret_123" not in content


def test_update_env_values_batch(tmp_path: Path):
    target_env = tmp_path / ".env"
    target_env.write_text("# Initial comment\nEXISTING_VAR=hello\n")

    update_env_values(
        target_env,
        {
            "TIKTOK_KEY": "val_key",
            "TIKTOK_SECRET": "val_secret",
        },
    )

    content = target_env.read_text()
    assert "# Initial comment" in content
    assert "EXISTING_VAR=hello" in content
    assert "TIKTOK_KEY=val_key" in content
    assert "TIKTOK_SECRET=val_secret" in content

    if os.name == "posix":
        assert (target_env.stat().st_mode & 0o777) == 0o600
