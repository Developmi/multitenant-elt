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
    run_meta_auth_wizard,
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


def test_update_client_env_file_not_exist(tmp_path: Path):
    env_file = tmp_path / ".env"
    updated = update_client_env(env_file, "newclient", "tok123")
    assert updated is True
    assert "FACEBOOK_ACCESS_TOKEN_NEWCLIENT=tok123" in env_file.read_text()


def test_update_client_env_no_trailing_newline(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("SOME_VAR=val")
    update_client_env(env_file, "test", "tok")
    content = env_file.read_text()
    assert content.startswith("SOME_VAR=val\nFACEBOOK_ACCESS_TOKEN_TEST=tok")


def test_debug_user_token_network_error():
    with patch("requests.get", side_effect=Exception("Connection refused")):
        with pytest.raises(MetaAuthError, match="Network error while inspecting token"):
            debug_user_token("any_token")


def test_exchange_long_lived_token_network_error():
    with patch("requests.get", side_effect=Exception("Timeout")):
        with pytest.raises(MetaAuthError, match="Network error during token exchange"):
            exchange_long_lived_token("app_1", "sec_1", "tok_1")


def test_exchange_long_lived_token_subcode_and_app_id_hint():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "error": {
            "message": "Invalid app id provided",
            "code": 101,
            "error_subcode": 200,
        }
    }
    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(MetaAuthError) as exc:
            exchange_long_lived_token("bad_app", "sec", "tok")
        assert "subcode: 200" in str(exc.value)
        assert "Verify that the Meta App ID matches" in str(exc.value)


def test_exchange_long_lived_token_missing_access_token():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"token_type": "bearer"}
    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(MetaAuthError, match="Missing access_token"):
            exchange_long_lived_token("app", "sec", "tok")


def test_list_page_accounts_network_and_api_error():
    with patch("requests.get", side_effect=Exception("Network drop")):
        with pytest.raises(MetaAuthError, match="Network error fetching accounts"):
            list_page_accounts("tok")

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"error": {"message": "Permission denied"}}
    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(MetaAuthError, match="Accounts retrieval failed: Permission denied"):
            list_page_accounts("tok")


def test_list_page_accounts_ig_exception_graceful():
    accounts_payload = {"data": [{"access_token": "ptok", "name": "PName", "id": "p1"}]}

    def side_effect(url, *args, **kwargs):
        mock_resp = MagicMock()
        if "/me/accounts" in url:
            mock_resp.json.return_value = accounts_payload
        else:
            raise Exception("IG query failure")
        return mock_resp

    with patch("requests.get", side_effect=side_effect):
        pages = list_page_accounts("tok")
        assert len(pages) == 1
        assert pages[0].instagram_id is None
        assert pages[0].instagram_username is None


def test_wizard_non_interactive_validation(capsys, monkeypatch):
    monkeypatch.delenv("META_APP_ID", raising=False)
    monkeypatch.delenv("META_APP_SECRET", raising=False)

    # Missing app_id & app_secret
    ret = run_meta_auth_wizard(interactive=False)
    assert ret == 1
    assert "Meta App ID and App Secret are required" in capsys.readouterr().err

    # Missing token
    ret = run_meta_auth_wizard(interactive=False, app_id="123", app_secret="sec")
    assert ret == 1
    assert "User token is required in non-interactive mode" in capsys.readouterr().err

    # Empty token
    ret = run_meta_auth_wizard(interactive=False, app_id="123", app_secret="sec", token="")
    assert ret == 1


def test_wizard_debug_and_exchange_and_page_failures(capsys):
    # Debug failure
    with patch(
        "agency_analytics.meta_auth.debug_user_token", side_effect=MetaAuthError("Debug fail")
    ):
        ret = run_meta_auth_wizard(interactive=False, app_id="aid", app_secret="asec", token="tok")
        assert ret == 1
        assert "Debug fail" in capsys.readouterr().err

    # Exchange failure
    valid_info = MetaTokenInfo(
        is_valid=True,
        app_id="aid",
        user_id="uid",
        scopes=["pages_show_list"],
        expires_at=0,
    )
    with patch("agency_analytics.meta_auth.debug_user_token", return_value=valid_info):
        with patch(
            "agency_analytics.meta_auth.exchange_long_lived_token",
            side_effect=MetaAuthError("Exchange fail"),
        ):
            ret = run_meta_auth_wizard(
                interactive=False, app_id="aid", app_secret="asec", token="tok"
            )
            assert ret == 1
            assert "Exchange fail" in capsys.readouterr().err

    # Page list failure and empty pages
    with patch("agency_analytics.meta_auth.debug_user_token", return_value=valid_info):
        with patch("agency_analytics.meta_auth.exchange_long_lived_token", return_value="llt"):
            with patch(
                "agency_analytics.meta_auth.list_page_accounts",
                side_effect=MetaAuthError("List fail"),
            ):
                ret = run_meta_auth_wizard(
                    interactive=False, app_id="aid", app_secret="asec", token="tok"
                )
                assert ret == 1

            with patch("agency_analytics.meta_auth.list_page_accounts", return_value=[]):
                ret = run_meta_auth_wizard(
                    interactive=False, app_id="aid", app_secret="asec", token="tok"
                )
                assert ret == 1
                assert "No Facebook Pages found" in capsys.readouterr().err


def test_wizard_success_single_page(tmp_path, capsys):
    valid_info = MetaTokenInfo(
        is_valid=True,
        app_id="aid",
        user_id="uid",
        scopes=["pages_show_list", "pages_read_engagement"],
        expires_at=0,
    )
    page = MetaPageAccount(
        page_id="pid1",
        page_name="Page One",
        access_token="page_tok_1",
        instagram_id="ig1",
        instagram_username="iguser1",
    )
    env_file = tmp_path / ".env"
    with patch("agency_analytics.meta_auth.debug_user_token", return_value=valid_info):
        with patch("agency_analytics.meta_auth.exchange_long_lived_token", return_value="llt"):
            with patch("agency_analytics.meta_auth.list_page_accounts", return_value=[page]):
                ret = run_meta_auth_wizard(
                    client_id="myclient",
                    app_id="aid",
                    app_secret="asec",
                    token="tok",
                    env_path=str(env_file),
                    interactive=False,
                )
                assert ret == 0
                out = capsys.readouterr().out
                assert "SUCCESS: Generated Permanent Page Access Token" in out
                assert "FACEBOOK_ACCESS_TOKEN_MYCLIENT=page_tok_1" in env_file.read_text()


def test_wizard_interactive_multiple_pages(monkeypatch, capsys):
    valid_info = MetaTokenInfo(
        is_valid=True,
        app_id="aid",
        user_id="uid",
        scopes=["pages_show_list"],
        expires_at=0,
    )
    p1 = MetaPageAccount(
        page_id="p1",
        page_name="Page 1",
        access_token="tok1",
        instagram_id=None,
        instagram_username=None,
    )
    p2 = MetaPageAccount(
        page_id="p2",
        page_name="Page 2",
        access_token="tok2",
        instagram_id="ig2",
        instagram_username="user2",
    )

    inputs = iter(
        [
            "my_app_id",  # app_id prompt
            "invalid_choice",  # page choice retry
            "2",  # valid page choice (selects p2)
            "",  # client name skip
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "my_secret_or_token")

    with patch("agency_analytics.meta_auth.debug_user_token", return_value=valid_info):
        with patch("agency_analytics.meta_auth.exchange_long_lived_token", return_value="llt"):
            with patch("agency_analytics.meta_auth.list_page_accounts", return_value=[p1, p2]):
                ret = run_meta_auth_wizard(interactive=True)
                assert ret == 0
                out = capsys.readouterr().out
                assert "Page: Page 2 (ID: p2)" in out
                assert "Note: .env file was not updated" in out
