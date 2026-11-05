"""Tests for agency_analytics.google_auth module.

Follows strict TDD: unit tests for GoogleTokenManager, credential resolution,
OAuth URL builder, and environment update utilities.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agency_analytics.google_auth import (
    GoogleAuthError,
    GoogleTokenManager,
    _OAuthRedirectHandler,
    build_authorization_url,
    get_scopes_for_services,
    resolve_google_credentials,
    run_google_auth_wizard,
    update_client_env,
)


class TestGoogleTokenManager:
    """Tests for the memory-cached Google OAuth token manager."""

    def test_get_token_fetches_and_caches(self):
        """First call fetches token from Google OAuth endpoint and caches it."""
        manager = GoogleTokenManager(
            client_id="test_client_id",
            client_secret="test_secret",
            refresh_token="test_refresh_token",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "ya29.initial_token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

        with patch(
            "agency_analytics.google_auth.requests.post", return_value=mock_response
        ) as mock_post:
            token1 = manager.get_token()
            token2 = manager.get_token()

            assert token1 == "ya29.initial_token"
            assert token2 == "ya29.initial_token"
            # Should only make 1 HTTP call due to caching
            assert mock_post.call_count == 1
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["data"]["client_id"] == "test_client_id"
            assert call_kwargs["data"]["grant_type"] == "refresh_token"
            assert call_kwargs["data"]["refresh_token"] == "test_refresh_token"

    def test_get_token_refreshes_when_near_expiry(self):
        """Token refreshes when current time is within buffer window of expiration."""
        manager = GoogleTokenManager(
            client_id="test_client_id",
            client_secret="test_secret",
            refresh_token="test_refresh_token",
        )

        mock_resp1 = MagicMock()
        mock_resp1.status_code = 200
        mock_resp1.json.return_value = {
            "access_token": "ya29.token_1",
            "expires_in": 3600,
        }

        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_resp2.json.return_value = {
            "access_token": "ya29.token_2",
            "expires_in": 3600,
        }

        with patch(
            "agency_analytics.google_auth.requests.post", side_effect=[mock_resp1, mock_resp2]
        ) as mock_post:
            # First call at t=0
            with patch("time.time", return_value=1000.0):
                assert manager.get_token() == "ya29.token_1"

            # Second call at t=4000 (expired, past 1000 + 3600 - 300)
            with patch("time.time", return_value=5000.0):
                assert manager.get_token() == "ya29.token_2"

            assert mock_post.call_count == 2

    def test_get_token_raises_on_invalid_grant(self):
        """Raises GoogleAuthError with informative message on invalid_grant."""
        manager = GoogleTokenManager(
            client_id="test_client_id",
            client_secret="test_secret",
            refresh_token="expired_or_revoked_refresh_token",
        )

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Bad Request: Token has been expired or revoked.",
        }

        with patch("agency_analytics.google_auth.requests.post", return_value=mock_response):
            with pytest.raises(GoogleAuthError, match="Token has been expired or revoked"):
                manager.get_token()

    def test_get_token_network_error_raises(self):
        manager = GoogleTokenManager(
            client_id="test_client_id",
            client_secret="test_secret",
            refresh_token="test_refresh_token",
        )
        with patch(
            "agency_analytics.google_auth.requests.post", side_effect=Exception("Connection reset")
        ):
            with pytest.raises(GoogleAuthError, match="Network error"):
                manager.get_token()

    def test_get_token_json_error_raises(self):
        manager = GoogleTokenManager(
            client_id="test_client_id",
            client_secret="test_secret",
            refresh_token="test_refresh_token",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.json.side_effect = ValueError("Bad JSON")
        with patch("agency_analytics.google_auth.requests.post", return_value=mock_resp):
            with pytest.raises(GoogleAuthError, match="Failed to parse"):
                manager.get_token()

    def test_invalidate_and_refresh(self):
        manager = GoogleTokenManager(
            client_id="test_client_id",
            client_secret="test_secret",
            refresh_token="test_refresh_token",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "fresh_tok", "expires_in": 3600}
        with patch("agency_analytics.google_auth.requests.post", return_value=mock_resp):
            tok = manager._refresh()
            assert tok == "fresh_tok"
            manager.invalidate()
            assert manager._access_token is None
            assert manager._expires_at == 0.0

    def test_credentials_property(self):
        from google.oauth2.credentials import Credentials

        manager = GoogleTokenManager(
            client_id="test_client_id",
            client_secret="test_secret",
            refresh_token="test_refresh_token",
        )
        creds = manager.credentials
        assert isinstance(creds, Credentials)
        assert creds.client_id == "test_client_id"
        assert creds.client_secret == "test_secret"
        assert creds.refresh_token == "test_refresh_token"


class TestResolveGoogleCredentials:
    """Tests for multi-tenant credential hierarchy resolution."""

    def test_resolves_client_specific_suffixed_keys(self, monkeypatch):
        """Client-specific suffixed keys take precedence over global agency keys."""
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID_ACME", "acme_id")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET_ACME", "acme_secret")
        monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN_ACME", "acme_refresh")

        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "global_id")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "global_secret")
        monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "global_refresh")

        creds = resolve_google_credentials(client_name="acme", service="ads")
        assert creds is not None
        assert creds["client_id"] == "acme_id"
        assert creds["client_secret"] == "acme_secret"
        assert creds["refresh_token"] == "acme_refresh"

    def test_falls_back_to_global_agency_keys(self, monkeypatch):
        """Falls back to global keys when client-suffixed keys are absent."""
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID_ACME", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET_ACME", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN_ACME", raising=False)

        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "global_id")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "global_secret")
        monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "global_refresh")

        creds = resolve_google_credentials(client_name="acme", service="gtm")
        assert creds is not None
        assert creds["client_id"] == "global_id"
        assert creds["client_secret"] == "global_secret"
        assert creds["refresh_token"] == "global_refresh"

    def test_resolves_legacy_service_specific_keys(self, monkeypatch):
        """Falls back to legacy YOUTUBE_OAUTH_* env keys if generic GOOGLE_OAUTH_* not set."""
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN", raising=False)

        monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_ID", "legacy_yt_id")
        monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_SECRET", "legacy_yt_secret")
        monkeypatch.setenv("YOUTUBE_OAUTH_REFRESH_TOKEN", "legacy_yt_refresh")

        creds = resolve_google_credentials(client_name="acme", service="youtube")
        assert creds is not None
        assert creds["client_id"] == "legacy_yt_id"
        assert creds["client_secret"] == "legacy_yt_secret"
        assert creds["refresh_token"] == "legacy_yt_refresh"

    def test_resolves_legacy_client_specific_keys(self, monkeypatch):
        """Resolves client-specific legacy YOUTUBE_OAUTH_*_{CLIENT} keys."""
        for var in [
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_OAUTH_REFRESH_TOKEN",
        ]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_ID_ACME", "yt_acme_id")
        monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_SECRET_ACME", "yt_acme_sec")
        monkeypatch.setenv("YOUTUBE_OAUTH_REFRESH_TOKEN_ACME", "yt_acme_rt")

        creds = resolve_google_credentials(client_name="acme", service="youtube")
        assert creds is not None
        assert creds["client_id"] == "yt_acme_id"
        assert creds["client_secret"] == "yt_acme_sec"
        assert creds["refresh_token"] == "yt_acme_rt"

    def test_returns_none_when_unconfigured(self, monkeypatch):
        """Returns None if neither client-specific nor global OAuth credentials exist."""
        for var in [
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_OAUTH_REFRESH_TOKEN",
            "GOOGLE_OAUTH_CLIENT_ID_ACME",
            "GOOGLE_OAUTH_CLIENT_SECRET_ACME",
            "GOOGLE_OAUTH_REFRESH_TOKEN_ACME",
        ]:
            monkeypatch.delenv(var, raising=False)

        creds = resolve_google_credentials(client_name="acme", service="ads")
        assert creds is None


class TestScopeAndUrlBuilder:
    """Tests for OAuth authorization URL generation and scope mapping."""

    def test_get_scopes_for_services(self):
        """Maps service aliases to appropriate Google OAuth scopes."""
        youtube_scopes = get_scopes_for_services(["youtube"])
        assert "https://www.googleapis.com/auth/youtube.readonly" in youtube_scopes
        assert "https://www.googleapis.com/auth/yt-analytics.readonly" in youtube_scopes

        gtm_scopes = get_scopes_for_services(["gtm"])
        assert gtm_scopes == ["https://www.googleapis.com/auth/tagmanager.readonly"]

        ads_scopes = get_scopes_for_services(["ads"])
        assert ads_scopes == ["https://www.googleapis.com/auth/adwords"]

        all_scopes = get_scopes_for_services(["all"])
        assert len(all_scopes) == 4
        assert "https://www.googleapis.com/auth/adwords" in all_scopes

    def test_build_authorization_url(self):
        """Builds valid Google OAuth consent URL with offline access and prompt."""
        url = build_authorization_url(
            client_id="sample_client_id.apps.googleusercontent.com",
            scopes=["https://www.googleapis.com/auth/adwords"],
            redirect_uri="http://localhost:8085",
        )
        assert "accounts.google.com/o/oauth2/v2/auth" in url
        assert "client_id=sample_client_id.apps.googleusercontent.com" in url
        assert "redirect_uri=http%3A%2F%2Flocalhost%3A8085" in url
        assert "response_type=code" in url
        assert "access_type=offline" in url
        assert "prompt=consent" in url


class TestUpdateClientEnv:
    """Tests for safe writing of OAuth tokens to .env."""

    def test_update_client_env_global(self, tmp_path):
        """Writes global credentials when client_name is None."""
        env_file = tmp_path / ".env"
        env_file.write_text("POSTGRES_USER=test\n")

        update_client_env(
            env_path=env_file,
            client_name=None,
            client_id="my_client_id",
            client_secret="my_client_secret",
            refresh_token="my_refresh_token",
        )

        content = env_file.read_text()
        assert "GOOGLE_OAUTH_CLIENT_ID=my_client_id" in content
        assert "GOOGLE_OAUTH_CLIENT_SECRET=my_client_secret" in content
        assert "GOOGLE_OAUTH_REFRESH_TOKEN=my_refresh_token" in content
        assert "POSTGRES_USER=test" in content

    def test_update_client_env_specific(self, tmp_path):
        """Writes suffixed client credentials when client_name is provided."""
        env_file = tmp_path / ".env"
        env_file.write_text("POSTGRES_USER=test\n")

        update_client_env(
            env_path=env_file,
            client_name="client_demo",
            client_id="demo_id",
            client_secret="demo_secret",
            refresh_token="demo_refresh",
        )

        content = env_file.read_text()
        assert "GOOGLE_OAUTH_CLIENT_ID_CLIENT_DEMO=demo_id" in content
        assert "GOOGLE_OAUTH_CLIENT_SECRET_CLIENT_DEMO=demo_secret" in content
        assert "GOOGLE_OAUTH_REFRESH_TOKEN_CLIENT_DEMO=demo_refresh" in content

    def test_update_client_env_overwrites_existing(self, tmp_path):
        """Overwrites existing keys if they are already present in the env file."""
        env_file = tmp_path / ".env"
        env_file.write_text("GOOGLE_OAUTH_CLIENT_ID=old_id\nGOOGLE_OAUTH_CLIENT_SECRET=old_sec\n")
        update_client_env(
            env_path=env_file,
            client_name=None,
            client_id="new_id",
            client_secret="new_sec",
            refresh_token="new_rt",
        )
        content = env_file.read_text()
        assert "GOOGLE_OAUTH_CLIENT_ID=new_id" in content
        assert "GOOGLE_OAUTH_CLIENT_SECRET=new_sec" in content
        assert "GOOGLE_OAUTH_REFRESH_TOKEN=new_rt" in content
        assert "old_id" not in content


class TestCliAuthGoogle:
    """Tests for CLI integration of auth google command."""

    def test_cli_auth_google_help(self):
        import sys

        from agency_analytics.cli import cli

        old_argv = sys.argv
        sys.argv = ["multitenant-elt", "auth", "google", "--help"]
        try:
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
        finally:
            sys.argv = old_argv


class TestOAuthRedirectHandler:
    """Tests for the loopback HTTP redirect handler."""

    def test_handler_success(self):
        handler = _OAuthRedirectHandler.__new__(_OAuthRedirectHandler)
        handler.path = "/?code=sample_auth_code"
        handler.server = MagicMock()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()

        handler.do_GET()
        assert handler.server.auth_code == "sample_auth_code"
        handler.send_response.assert_called_with(200)

    def test_handler_error(self):
        handler = _OAuthRedirectHandler.__new__(_OAuthRedirectHandler)
        handler.path = "/?error=access_denied"
        handler.server = MagicMock()
        handler.send_response = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()

        handler.do_GET()
        assert handler.server.auth_error == "access_denied"
        handler.send_response.assert_called_with(400)

    def test_handler_log_message_silent(self):
        handler = _OAuthRedirectHandler.__new__(_OAuthRedirectHandler)
        # Should not raise
        handler.log_message("format", "arg1")


class TestRunGoogleAuthWizard:
    """Tests for the interactive and non-interactive Google OAuth wizard."""

    def test_wizard_invalid_services(self, capsys):
        ret = run_google_auth_wizard(services=["invalid_srv"])
        assert ret == 1
        out = capsys.readouterr().out
        assert "No valid services specified" in out

    def test_wizard_non_interactive_missing_client_id(self, monkeypatch, capsys):
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        ret = run_google_auth_wizard(interactive=False, client_id=None)
        assert ret == 1
        out = capsys.readouterr().out
        assert "Missing required --client-id" in out

    def test_wizard_non_interactive_missing_client_secret(self, monkeypatch, capsys):
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        ret = run_google_auth_wizard(interactive=False, client_id="cid", client_secret=None)
        assert ret == 1
        out = capsys.readouterr().out
        assert "Missing required --client-secret" in out

    @patch("agency_analytics.google_auth.HTTPServer")
    def test_wizard_server_auth_error(self, mock_server_cls, capsys):
        mock_server = MagicMock()

        def fake_handle():
            mock_server.auth_error = "access_denied"
            mock_server.auth_code = None

        mock_server.handle_request.side_effect = fake_handle
        mock_server_cls.return_value = mock_server

        ret = run_google_auth_wizard(
            client_id="cid",
            client_secret="sec",
            interactive=False,
            no_browser=True,
        )
        assert ret == 1
        out = capsys.readouterr().out
        assert "Authorization error: access_denied" in out

    @patch("agency_analytics.google_auth.HTTPServer")
    def test_wizard_server_missing_code(self, mock_server_cls, capsys):
        mock_server = MagicMock()
        mock_server_cls.return_value = mock_server

        ret = run_google_auth_wizard(
            client_id="cid",
            client_secret="sec",
            interactive=False,
            no_browser=True,
        )
        assert ret == 1
        out = capsys.readouterr().out
        assert "Failed to obtain authorization code" in out

    @patch("agency_analytics.google_auth.requests.post")
    @patch("agency_analytics.google_auth.HTTPServer")
    def test_wizard_token_exchange_network_error(self, mock_server_cls, mock_post, capsys):
        mock_server = MagicMock()

        def fake_handle():
            mock_server.auth_code = "valid_code"
            mock_server.auth_error = None

        mock_server.handle_request.side_effect = fake_handle
        mock_server_cls.return_value = mock_server
        mock_post.side_effect = Exception("Connection timeout")

        ret = run_google_auth_wizard(
            client_id="cid",
            client_secret="sec",
            interactive=False,
            no_browser=True,
        )
        assert ret == 1
        out = capsys.readouterr().out
        assert "Token exchange request failed" in out

    @patch("agency_analytics.google_auth.requests.post")
    @patch("agency_analytics.google_auth.HTTPServer")
    def test_wizard_token_exchange_http_error(self, mock_server_cls, mock_post, capsys):
        mock_server = MagicMock()

        def fake_handle():
            mock_server.auth_code = "valid_code"
            mock_server.auth_error = None

        mock_server.handle_request.side_effect = fake_handle
        mock_server_cls.return_value = mock_server

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Code expired",
        }
        mock_post.return_value = mock_resp

        ret = run_google_auth_wizard(
            client_id="cid",
            client_secret="sec",
            interactive=False,
            no_browser=True,
        )
        assert ret == 1
        out = capsys.readouterr().out
        assert "Token exchange failed (invalid_grant)" in out
        assert "The authorization code expired" in out

    @patch("agency_analytics.google_auth.update_client_env")
    @patch("agency_analytics.google_auth.requests.post")
    @patch("agency_analytics.google_auth.HTTPServer")
    def test_wizard_success(self, mock_server_cls, mock_post, mock_update, tmp_path, capsys):
        mock_server = MagicMock()

        def fake_handle():
            mock_server.auth_code = "valid_code"
            mock_server.auth_error = None

        mock_server.handle_request.side_effect = fake_handle
        mock_server_cls.return_value = mock_server

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "refresh_token": "1//permanent_refresh_token_xyz",
            "access_token": "ya29.sample",
        }
        mock_post.return_value = mock_resp

        env_file = tmp_path / ".env"
        ret = run_google_auth_wizard(
            client_id="cid",
            client_secret="sec",
            env_path=env_file,
            interactive=False,
            no_browser=True,
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "SUCCESS: Received Permanent Google OAuth Refresh Token" in out
        mock_update.assert_called_once()

    @patch("agency_analytics.google_auth.update_client_env")
    @patch("agency_analytics.google_auth.requests.post")
    @patch("agency_analytics.google_auth.HTTPServer")
    @patch("agency_analytics.google_auth.webbrowser.open")
    def test_wizard_interactive_prompts(
        self, mock_browser, mock_server_cls, mock_post, mock_update, monkeypatch, tmp_path
    ):
        mock_server = MagicMock()

        def fake_handle():
            mock_server.auth_code = "valid_code"
            mock_server.auth_error = None

        mock_server.handle_request.side_effect = fake_handle
        mock_server_cls.return_value = mock_server

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"refresh_token": "rt_1234567890"}
        mock_post.return_value = mock_resp

        # Interactive inputs: use existing = n, prompt new id = new_id, secret = new_sec
        inputs = iter(["n", "new_id"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "new_secret")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "existing_id")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "existing_sec")

        ret = run_google_auth_wizard(
            client_name="testclient",
            env_path=tmp_path / ".env",
            interactive=True,
            no_browser=False,
        )
        assert ret == 0
        mock_browser.assert_called_once()
