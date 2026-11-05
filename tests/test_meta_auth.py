from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agency_analytics.meta_auth import (
    MetaAuthError,
    MetaPageAccount,
    MetaTokenInfo,
    debug_user_token,
    exchange_long_lived_token,
    list_page_accounts,
    update_client_env,
)


@pytest.fixture
def mock_debug_token_valid():
    return {
        "data": {
            "app_id": "1234567890123456",
            "type": "USER",
            "application": "Test Analytics App",
            "is_valid": True,
            "scopes": [
                "pages_show_list",
                "pages_read_engagement",
                "instagram_basic",
                "instagram_manage_insights",
            ],
            "user_id": "9876543210987654",
            "expires_at": 1793732400,
        }
    }


@pytest.fixture
def mock_debug_token_invalid():
    return {
        "error": {
            "message": "Error validating access token: The session has been invalidated.",
            "type": "OAuthException",
            "code": 190,
            "error_subcode": 460,
        }
    }


def test_debug_user_token_valid(mock_debug_token_valid):
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_debug_token_valid
        mock_get.return_value = mock_response

        token_info = debug_user_token("short_user_token")
        assert isinstance(token_info, MetaTokenInfo)
        assert token_info.is_valid is True
        assert token_info.user_id == "9876543210987654"
        assert token_info.app_id == "1234567890123456"
        assert "pages_show_list" in token_info.scopes


def test_debug_user_token_invalid(mock_debug_token_invalid):
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = mock_debug_token_invalid
        mock_get.return_value = mock_response

        with pytest.raises(MetaAuthError) as exc_info:
            debug_user_token("expired_token")
        assert "The session has been invalidated" in str(exc_info.value)


def test_exchange_long_lived_token_success():
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "long_lived_token_60_days",
            "token_type": "bearer",
            "expires_in": 5184000,
        }
        mock_get.return_value = mock_response

        token = exchange_long_lived_token("app_123", "secret_456", "short_token_789")
        assert token == "long_lived_token_60_days"


def test_exchange_long_lived_token_error():
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "error": {
                "message": "Error validating client secret",
                "type": "OAuthException",
                "code": 190,
            }
        }
        mock_get.return_value = mock_response

        with pytest.raises(MetaAuthError) as exc_info:
            exchange_long_lived_token("app_123", "secret_wrong", "short_token_789")
        assert "Error validating client secret" in str(exc_info.value)


def test_list_page_accounts_with_instagram():
    accounts_payload = {
        "data": [
            {
                "access_token": "page_token_client_example",
                "name": "Client Example Page",
                "id": "111222333444555",
                "tasks": ["ANALYZE", "ADVERTISE"],
            }
        ]
    }
    instagram_payload = {
        "instagram_business_account": {
            "id": "999888777666555",
            "username": "clientexamplepage",
        },
        "id": "111222333444555",
    }

    def side_effect(url, *args, **kwargs):
        mock_resp = MagicMock()
        if "/me/accounts" in url:
            mock_resp.status_code = 200
            mock_resp.json.return_value = accounts_payload
        else:
            mock_resp.status_code = 200
            mock_resp.json.return_value = instagram_payload
        return mock_resp

    with patch("requests.get", side_effect=side_effect):
        pages = list_page_accounts("user_token_abc")
        assert len(pages) == 1
        page = pages[0]
        assert isinstance(page, MetaPageAccount)
        assert page.page_id == "111222333444555"
        assert page.page_name == "Client Example Page"
        assert page.access_token == "page_token_client_example"
        assert page.instagram_id == "999888777666555"
        assert page.instagram_username == "clientexamplepage"


def test_update_client_env(tmp_path: Path):
    env_file = tmp_path / ".env"
    initial_content = (
        "# Global env\n"
        "POSTGRES_USER=agency_admin\n"
        "FACEBOOK_ACCESS_TOKEN_CLIENT_EXAMPLE=old_fb_token\n"
        "INSTAGRAM_ACCESS_TOKEN_CLIENT_EXAMPLE=old_ig_token\n"
        "# Other token\n"
        "YOUTUBE_API_KEY_CLIENT_EXAMPLE=xyz\n"
    )
    env_file.write_text(initial_content)

    updated = update_client_env(env_file, "client_example", "new_permanent_page_token")
    assert updated is True

    result_content = env_file.read_text()
    assert "FACEBOOK_ACCESS_TOKEN_CLIENT_EXAMPLE=new_permanent_page_token" in result_content
    assert "INSTAGRAM_ACCESS_TOKEN_CLIENT_EXAMPLE=new_permanent_page_token" in result_content
    assert "POSTGRES_USER=agency_admin" in result_content
    assert "YOUTUBE_API_KEY_CLIENT_EXAMPLE=xyz" in result_content
    assert "# Other token" in result_content


def test_update_client_env_append_if_missing(tmp_path: Path):
    env_file = tmp_path / ".env"
    initial_content = "POSTGRES_USER=agency_admin\n"
    env_file.write_text(initial_content)

    updated = update_client_env(env_file, "acme", "new_page_token_acme")
    assert updated is True

    result_content = env_file.read_text()
    assert "FACEBOOK_ACCESS_TOKEN_ACME=new_page_token_acme" in result_content
    assert "INSTAGRAM_ACCESS_TOKEN_ACME=new_page_token_acme" in result_content
