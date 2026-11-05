"""Offline marker/guard tests for scripts/pipeline.sh.

Validates that:
1. scripts/pipeline.sh is a clean host-side wrapper that delegates orchestration
   to agency_analytics.pipeline_runner inside agency_pipeline.
2. Host container health checks validate both agency_postgres and agency_pipeline.
3. Telegram credentials adhere to file-guarded, key-targeted fallback (host env wins)
   without process env pollution (no set -a, no export, no full source).
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PIPELINE_SH = _REPO_ROOT / "scripts" / "pipeline.sh"
_TEXT = _PIPELINE_SH.read_text(encoding="utf-8")


def _assert_absent(text: str, fragment: str, label: str) -> None:
    assert fragment not in text, (
        f"{label}: forbidden fragment {fragment!r} present in {_PIPELINE_SH.name}:\n{text}"
    )


def test_nightly_delegates_to_python_pipeline_runner():
    """Consolidated runner: host script delegates directly to agency_analytics.pipeline_runner."""
    assert "python3 -m agency_analytics.pipeline_runner" in _TEXT, (
        "scripts/pipeline.sh must delegate execution to agency_analytics.pipeline_runner"
    )
    assert "docker exec" in _TEXT
    assert "agency_pipeline" in _TEXT


def test_nightly_validates_required_containers_health():
    """Health check guards: verifies postgres and pipeline containers are healthy."""
    assert 'check_container "agency_postgres"' in _TEXT
    assert 'check_container "agency_pipeline"' in _TEXT


def test_telegram_env_guard_sits_after_both_defaults():
    """TLG-S1/S3: the ./.env fallback is file-guarded and placed below both
    TELEGRAM_* defaults (host env wins; absent file keeps the silent skip)."""
    defaults_end = _TEXT.index('TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"')
    guard_at = _TEXT.index("[ -f ./.env ]")
    assert guard_at > defaults_end, (
        ".env guard must come after both TELEGRAM defaults (line-order, D3)"
    )


def test_telegram_fallback_reads_only_the_two_keys():
    """TLG-S2: unset keys are extracted with exact-key anchored sed only."""
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        assert f"sed -n 's/^{key}=//p' ./.env" in _TEXT, (
            f"fallback must extract {key} with an anchored key-targeted sed"
        )
        assert f'[ -z "${key}" ] &&' in _TEXT, (
            f"fallback must only read {key} when it is unset (env wins)"
        )


def test_telegram_fallback_never_pollutes_the_process_env():
    """TLG-S4: no set -a, no full source, no export of the Telegram keys.

    Scans executable lines only (comment prose may legitimately name the
    forbidden constructs while documenting their absence).
    """
    executable_lines = [
        ln for ln in _TEXT.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    for fragment in ("set -a", "export TELEGRAM", "source ./.env", ". ./.env"):
        offenders = [ln for ln in executable_lines if fragment in ln]
        assert offenders == [], f"TLG-S4: forbidden construct {fragment!r} in:\n{offenders}"
