"""Shared fixtures and configuration for all dlt connector tests."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

GA4_SERVICE_ACCOUNT_JSON = {
    "client_email": "test@example.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\n",
}

# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def ga4_service_account_file():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(GA4_SERVICE_ACCOUNT_JSON, tmp)
    tmp.close()
    yield tmp.name
    os.unlink(tmp.name)


@pytest.fixture
def mock_meta_api():
    patcher = patch("run_meta.requests.get")
    mock = patcher.start()
    yield mock
    patcher.stop()


@pytest.fixture
def mock_tiktok_api():
    patcher = patch("run_tiktok.requests.get")
    mock = patcher.start()
    yield mock
    patcher.stop()


@pytest.fixture
def mock_google_api():
    patcher = patch("run_google.requests.post")
    mock = patcher.start()
    yield mock
    patcher.stop()


@pytest.fixture
def mock_sleep():
    patcher = patch("time.sleep")
    mock = patcher.start()
    yield mock
    patcher.stop()


@pytest.fixture(autouse=True)
def prevent_webbrowser_open(monkeypatch):
    """Safety guard: ensure tests NEVER open a real browser window in any environment."""
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda *args, **kwargs: True)
    monkeypatch.setattr(webbrowser, "open_new", lambda *args, **kwargs: True)
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda *args, **kwargs: True)
