"""Tests for agency_analytics.tiktok_auth module."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agency_analytics.cli import cli
from agency_analytics.tiktok_auth import (
    TikTokAuthError,
    _TikTokOAuthRedirectHandler,
    build_authorization_url,
    exchange_code_for_tokens,
    generate_pkce_pair,
    resolve_tiktok_credentials,
    run_tiktok_auth_wizard,
    update_client_env,
)


class TestPkceGeneration:
    def test_pkce_pair_generation(self):
        verifier, challenge = generate_pkce_pair()
        assert len(verifier) >= 43
        assert len(challenge) > 0
        assert "=" not in challenge  # Base64URL padding stripped per RFC 7636


class TestBuildAuthorizationUrl:
    def test_build_url_parameters(self):
        url = build_authorization_url(
            client_key="test_client_key",
            scopes=["user.info.basic", "video.list"],
            redirect_uri="http://localhost:8086/",
            state="state_xyz_123",
            code_challenge="test_challenge_123",
        )
        assert "https://www.tiktok.com/v2/auth/authorize/" in url
        assert "client_key=test_client_key" in url
        assert "scope=user.info.basic%2Cvideo.list" in url
        assert "response_type=code" in url
        assert "redirect_uri=http%3A%2F%2Flocalhost%3A8086%2F" in url
        assert "state=state_xyz_123" in url
        assert "code_challenge=test_challenge_123" in url
        assert "code_challenge_method=S256" in url


class TestResolveTikTokCredentials:
    def test_resolves_client_specific_keys(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_ORGANIC_CLIENT_KEY_BONILLA", "ck_bonilla")
        monkeypatch.setenv("TIKTOK_ORGANIC_CLIENT_SECRET_BONILLA", "cs_bonilla")
        creds = resolve_tiktok_credentials("bonilla")
        assert creds == {"client_key": "ck_bonilla", "client_secret": "cs_bonilla"}

    def test_falls_back_to_global(self, monkeypatch):
        monkeypatch.delenv("TIKTOK_ORGANIC_CLIENT_KEY_TEST", raising=False)
        monkeypatch.delenv("TIKTOK_ORGANIC_CLIENT_SECRET_TEST", raising=False)
        monkeypatch.setenv("TIKTOK_ORGANIC_CLIENT_KEY", "ck_global")
        monkeypatch.setenv("TIKTOK_ORGANIC_CLIENT_SECRET", "cs_global")
        creds = resolve_tiktok_credentials("test")
        assert creds == {"client_key": "ck_global", "client_secret": "cs_global"}

    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("TIKTOK_ORGANIC_CLIENT_KEY", raising=False)
        monkeypatch.delenv("TIKTOK_ORGANIC_CLIENT_SECRET", raising=False)
        assert resolve_tiktok_credentials(None) is None


class TestUpdateClientEnv:
    def test_update_client_env_global(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("POSTGRES_USER=test\n")

        update_client_env(
            env_path=env_file,
            client_name=None,
            client_key="ck_glob",
            client_secret="cs_glob",
            refresh_token="rt_glob",
        )

        content = env_file.read_text()
        assert "TIKTOK_ORGANIC_CLIENT_KEY=ck_glob" in content
        assert "TIKTOK_ORGANIC_CLIENT_SECRET=cs_glob" in content
        assert "TIKTOK_ORGANIC_REFRESH_TOKEN=rt_glob" in content
        if os.name == "posix":
            assert (env_file.stat().st_mode & 0o777) == 0o600

    def test_update_client_env_client_specific(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        update_client_env(
            env_path=env_file,
            client_name="bonilla",
            client_key="ck_bon",
            client_secret="cs_bon",
            refresh_token="rt_bon",
        )
        content = env_file.read_text()
        assert "TIKTOK_ORGANIC_CLIENT_KEY_BONILLA=ck_bon" in content
        assert "TIKTOK_ORGANIC_CLIENT_SECRET_BONILLA=cs_bon" in content
        assert "TIKTOK_ORGANIC_REFRESH_TOKEN_BONILLA=rt_bon" in content


class TestTikTokOAuthRedirectHandler:
    def test_handler_success(self):
        handler = _TikTokOAuthRedirectHandler.__new__(_TikTokOAuthRedirectHandler)
        handler.path = "/?code=sample_tiktok_code&state=expected_state"
        handler.server = MagicMock()
        handler.server.expected_state = "expected_state"
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()

        handler.do_GET()
        assert handler.server.auth_code == "sample_tiktok_code"
        handler.send_response.assert_called_with(200)

    def test_handler_error(self):
        handler = _TikTokOAuthRedirectHandler.__new__(_TikTokOAuthRedirectHandler)
        handler.path = "/?error=access_denied"
        handler.server = MagicMock()
        handler.send_response = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()

        handler.do_GET()
        assert handler.server.auth_error == "access_denied"
        handler.send_response.assert_called_with(400)

    def test_handler_state_mismatch(self):
        handler = _TikTokOAuthRedirectHandler.__new__(_TikTokOAuthRedirectHandler)
        handler.path = "/?code=sample_code&state=wrong_state"
        handler.server = MagicMock()
        handler.server.expected_state = "expected_state"
        handler.send_response = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()

        handler.do_GET()
        assert handler.server.auth_error == "state_mismatch"
        handler.send_response.assert_called_with(400)

    def test_handler_log_message_silent(self):
        handler = _TikTokOAuthRedirectHandler.__new__(_TikTokOAuthRedirectHandler)
        handler.log_message("format", "arg")

    def test_real_loopback_socket_handshake_success(self):
        """End-to-end integration test with real OS TCP socket (no browser, real HTTP)."""
        import threading
        import urllib.request
        from http.server import HTTPServer

        server = HTTPServer(("127.0.0.1", 0), _TikTokOAuthRedirectHandler)
        server.auth_code = None  # type: ignore[attr-defined]
        server.auth_error = None  # type: ignore[attr-defined]
        server.expected_state = "cryptographic_state_token_123"  # type: ignore[attr-defined]
        port = server.server_port

        thread = threading.Thread(target=server.handle_request)
        thread.daemon = True
        thread.start()

        url = f"http://127.0.0.1:{port}/?code=real_tiktok_auth_code_999&state=cryptographic_state_token_123"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            assert response.status == 200
            body = response.read().decode("utf-8")
            assert "TikTok Authentication successful!" in body

        thread.join(timeout=5)
        server.server_close()

        assert server.auth_code == "real_tiktok_auth_code_999"  # type: ignore[attr-defined]
        assert server.auth_error is None  # type: ignore[attr-defined]

    def test_real_loopback_socket_state_mismatch(self):
        """Verify CSRF state mismatch over real TCP socket returns HTTP 400."""
        import threading
        import urllib.error
        import urllib.request
        from http.server import HTTPServer

        server = HTTPServer(("127.0.0.1", 0), _TikTokOAuthRedirectHandler)
        server.auth_code = None  # type: ignore[attr-defined]
        server.auth_error = None  # type: ignore[attr-defined]
        server.expected_state = "expected_legit_state"  # type: ignore[attr-defined]
        port = server.server_port

        thread = threading.Thread(target=server.handle_request)
        thread.daemon = True
        thread.start()

        url = f"http://127.0.0.1:{port}/?code=code_val&state=forged_state"
        req = urllib.request.Request(url)
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)

        assert exc_info.value.code == 400
        thread.join(timeout=5)
        server.server_close()

        assert server.auth_error == "state_mismatch"  # type: ignore[attr-defined]
        assert server.auth_code is None  # type: ignore[attr-defined]


class TestExchangeCodeForTokens:
    def test_exchange_success_with_nested_data(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "access_token": "act_123",
                "refresh_token": "rft_123",
                "open_id": "oid_123",
                "expires_in": 86400,
            },
            "error": {"code": "ok", "message": ""},
        }

        with patch(
            "agency_analytics.tiktok_auth.requests.post", return_value=mock_response
        ) as mock_post:
            tokens = exchange_code_for_tokens(
                "ck", "cs", "code", "http://localhost:8086/", code_verifier="verifier_123"
            )
            assert tokens["access_token"] == "act_123"
            assert tokens["refresh_token"] == "rft_123"
            assert tokens["open_id"] == "oid_123"
            assert mock_post.call_args[1]["data"]["code_verifier"] == "verifier_123"

    def test_exchange_api_error_raised(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "error": {"code": "invalid_grant", "message": "Authorization code expired"}
        }

        with patch("agency_analytics.tiktok_auth.requests.post", return_value=mock_response):
            with pytest.raises(TikTokAuthError, match="Authorization code expired"):
                exchange_code_for_tokens("ck", "cs", "code", "http://localhost:8086/")

    def test_exchange_error_string_response(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "error": "invalid_client",
            "error_description": "Secret mismatch",
        }
        with patch("agency_analytics.tiktok_auth.requests.post", return_value=mock_response):
            with pytest.raises(TikTokAuthError, match="invalid_client — Secret mismatch"):
                exchange_code_for_tokens("ck", "cs", "code", "http://localhost:8086/")

    def test_exchange_missing_refresh_token(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"access_token": "act_only"}}
        with patch("agency_analytics.tiktok_auth.requests.post", return_value=mock_response):
            with pytest.raises(TikTokAuthError, match="Missing refresh_token"):
                exchange_code_for_tokens("ck", "cs", "code", "http://localhost:8086/")

    def test_exchange_network_error_raised(self):
        with patch("agency_analytics.tiktok_auth.requests.post", side_effect=Exception("Timeout")):
            with pytest.raises(TikTokAuthError, match="Network error while exchanging"):
                exchange_code_for_tokens("ck", "cs", "code", "http://localhost:8086/")


class TestRunTikTokAuthWizard:
    def test_wizard_non_interactive_missing_keys(self):
        ret = run_tiktok_auth_wizard(interactive=False, client_key=None, client_secret=None)
        assert ret == 1

        ret_no_sec = run_tiktok_auth_wizard(interactive=False, client_key="ck", client_secret=None)
        assert ret_no_sec == 1

    def test_wizard_server_auth_error(self):
        mock_server = MagicMock()

        def fake_handle():
            mock_server.auth_error = "access_denied"

        mock_server.handle_request.side_effect = fake_handle
        with patch("agency_analytics.tiktok_auth.HTTPServer", return_value=mock_server):
            ret = run_tiktok_auth_wizard(
                interactive=False, client_key="ck", client_secret="cs", no_browser=True
            )
            assert ret == 1

    def test_wizard_server_missing_code(self):
        mock_server = MagicMock()

        def fake_handle():
            mock_server.auth_error = None
            mock_server.auth_code = None

        mock_server.handle_request.side_effect = fake_handle
        with patch("agency_analytics.tiktok_auth.HTTPServer", return_value=mock_server):
            ret = run_tiktok_auth_wizard(
                interactive=False, client_key="ck", client_secret="cs", no_browser=True
            )
            assert ret == 1

    def test_wizard_token_exchange_failure(self):
        mock_server = MagicMock()

        def fake_handle():
            mock_server.auth_code = "valid_code"
            mock_server.auth_error = None

        mock_server.handle_request.side_effect = fake_handle
        with patch("agency_analytics.tiktok_auth.HTTPServer", return_value=mock_server):
            with patch(
                "agency_analytics.tiktok_auth.exchange_code_for_tokens",
                side_effect=TikTokAuthError("Network timeout"),
            ):
                ret = run_tiktok_auth_wizard(
                    interactive=False, client_key="ck", client_secret="cs", no_browser=True
                )
                assert ret == 1

    def test_wizard_existing_credentials_declined(self, monkeypatch, tmp_path: Path):
        mock_server = MagicMock()

        def fake_handle():
            mock_server.auth_code = "fresh_code"
            mock_server.auth_error = None

        mock_server.handle_request.side_effect = fake_handle
        monkeypatch.setenv("TIKTOK_ORGANIC_CLIENT_KEY", "ck_old")
        monkeypatch.setenv("TIKTOK_ORGANIC_CLIENT_SECRET", "cs_old")

        inputs = iter(["n", "ck_new"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "cs_new")

        env_file = tmp_path / ".env"
        with patch("agency_analytics.tiktok_auth.HTTPServer", return_value=mock_server):
            with patch(
                "agency_analytics.tiktok_auth.exchange_code_for_tokens",
                return_value={"refresh_token": "rt_new"},
            ):
                with patch("agency_analytics.tiktok_auth.webbrowser.open") as mock_open:
                    ret = run_tiktok_auth_wizard(
                        env_path=env_file, interactive=True, no_browser=False
                    )
                    assert ret == 0
                    assert mock_open.called
                    assert "TIKTOK_ORGANIC_CLIENT_KEY=ck_new" in env_file.read_text()

    def test_wizard_server_bind_error(self):
        with patch(
            "agency_analytics.tiktok_auth.HTTPServer", side_effect=OSError("Address in use")
        ):
            ret = run_tiktok_auth_wizard(
                interactive=False, client_key="ck", client_secret="cs", no_browser=True
            )
            assert ret == 1

    def test_wizard_success(self, tmp_path: Path):
        mock_server = MagicMock()

        def fake_handle():
            mock_server.auth_code = "auth_code_xyz"
            mock_server.auth_error = None

        mock_server.handle_request.side_effect = fake_handle

        tokens_payload = {
            "data": {
                "access_token": "act_test",
                "refresh_token": "rft_long_lived_secret_token",
                "open_id": "open_id_bonilla",
                "expires_in": 86400,
            }
        }
        env_file = tmp_path / ".env"

        with patch("agency_analytics.tiktok_auth.HTTPServer", return_value=mock_server):
            with patch(
                "agency_analytics.tiktok_auth.exchange_code_for_tokens",
                return_value=tokens_payload["data"],
            ):
                ret = run_tiktok_auth_wizard(
                    client_name="bonilla",
                    client_key="ck_val",
                    client_secret="cs_val",
                    env_path=env_file,
                    interactive=False,
                    no_browser=True,
                )
                assert ret == 0
                content = env_file.read_text()
                assert "TIKTOK_ORGANIC_CLIENT_KEY_BONILLA=ck_val" in content
                assert "TIKTOK_ORGANIC_CLIENT_SECRET_BONILLA=cs_val" in content
                assert "TIKTOK_ORGANIC_REFRESH_TOKEN_BONILLA=rft_long_lived_secret_token" in content

    def test_wizard_interactive_prompts(self, monkeypatch, tmp_path: Path):
        mock_server = MagicMock()

        def fake_handle():
            mock_server.auth_code = "interactive_code"
            mock_server.auth_error = None

        mock_server.handle_request.side_effect = fake_handle

        monkeypatch.delenv("TIKTOK_ORGANIC_CLIENT_KEY", raising=False)
        monkeypatch.delenv("TIKTOK_ORGANIC_CLIENT_SECRET", raising=False)
        inputs = iter(["ck_input"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "cs_input")

        env_file = tmp_path / ".env"
        tokens_payload = {
            "refresh_token": "rt_interactive",
            "access_token": "at_interactive",
        }

        with patch("agency_analytics.tiktok_auth.HTTPServer", return_value=mock_server):
            with patch(
                "agency_analytics.tiktok_auth.exchange_code_for_tokens", return_value=tokens_payload
            ):
                ret = run_tiktok_auth_wizard(
                    client_name=None,
                    client_key=None,
                    client_secret=None,
                    env_path=env_file,
                    interactive=True,
                    no_browser=True,
                )
                assert ret == 0
                content = env_file.read_text()
                assert "TIKTOK_ORGANIC_CLIENT_KEY=ck_input" in content
                assert "TIKTOK_ORGANIC_CLIENT_SECRET=cs_input" in content
                assert "TIKTOK_ORGANIC_REFRESH_TOKEN=rt_interactive" in content

    def test_wizard_real_socket_end_to_end(self, tmp_path: Path):
        """Full end-to-end integration test with real OS TCP loopback server and real HTTP client."""
        import socket
        import threading
        import time
        import urllib.request

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            free_port = s.getsockname()[1]

        env_file = tmp_path / ".env"
        tokens_payload = {
            "access_token": "at_socket_ok",
            "refresh_token": "rt_socket_ok",
        }

        results: list[int] = []

        def run_wizard():
            with patch(
                "agency_analytics.tiktok_auth.secrets.token_urlsafe", return_value="fixed_state_xyz"
            ):
                with patch(
                    "agency_analytics.tiktok_auth.exchange_code_for_tokens",
                    return_value=tokens_payload,
                ):
                    code = run_tiktok_auth_wizard(
                        client_name="socket_test",
                        client_key="ck_socket",
                        client_secret="cs_socket",
                        env_path=env_file,
                        interactive=False,
                        port=free_port,
                        no_browser=True,
                    )
                    results.append(code)

        server_thread = threading.Thread(target=run_wizard)
        server_thread.daemon = True
        server_thread.start()

        # Brief pause to ensure the server thread is listening
        time.sleep(0.1)

        callback_url = (
            f"http://localhost:{free_port}/?code=my_callback_auth_code&state=fixed_state_xyz"
        )
        req = urllib.request.Request(callback_url)
        with urllib.request.urlopen(req, timeout=5) as response:
            assert response.status == 200

        server_thread.join(timeout=5)
        assert results == [0]
        content = env_file.read_text()
        assert "TIKTOK_ORGANIC_CLIENT_KEY_SOCKET_TEST=ck_socket" in content
        assert "TIKTOK_ORGANIC_CLIENT_SECRET_SOCKET_TEST=cs_socket" in content
        assert "TIKTOK_ORGANIC_REFRESH_TOKEN_SOCKET_TEST=rt_socket_ok" in content


class TestCliIntegration:
    def test_cli_auth_tiktok_help(self):
        old_argv = sys.argv
        sys.argv = ["multitenant-elt", "auth", "tiktok", "--help"]
        try:
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
        finally:
            sys.argv = old_argv
