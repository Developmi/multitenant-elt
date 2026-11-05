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
    build_authorization_url,
    get_scopes_for_services,
    resolve_google_credentials,
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

    @patch("agency_analytics.google_auth.run_google_auth_wizard")
    def test_cli_auth_google_invokes_wizard(self, mock_wizard):
        import sys

        from agency_analytics.cli import cli

        mock_wizard.return_value = 0
        old_argv = sys.argv
        sys.argv = [
            "multitenant-elt",
            "auth",
            "google",
            "--client",
            "acme",
            "--services",
            "ga4,gtm",
            "--non-interactive",
            "--client-id",
            "cid",
            "--client-secret",
            "sec",
        ]
        try:
            exit_code = cli()
            assert exit_code == 0
            mock_wizard.assert_called_once_with(
                client_name="acme",
                services=["ga4", "gtm"],
                client_id="cid",
                client_secret="sec",
                env_path=".env",
                interactive=False,
            )
        finally:
            sys.argv = old_argv
