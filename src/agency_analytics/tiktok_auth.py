"""TikTok OAuth 2.0 authentication and token management wizard.

Provides an interactive CLI wizard using local loopback redirection to authorize
TikTok Organic and Display API access, exchange authorization codes for long-lived
tokens (365-day refresh tokens), and persist credentials with POSIX 0600 permissions.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import os
import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from dlt.sources.helpers import requests

from agency_analytics.secrets_store import mask_secret, update_env_values

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
DEFAULT_SCOPES = ["user.info.basic", "video.list"]


class TikTokAuthError(Exception):
    """Raised when TikTok OAuth operations fail."""


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a cryptographically secure PKCE code_verifier and code_challenge (RFC 7636).

    Returns:
        A tuple of (code_verifier, code_challenge) using SHA-256 and base64url encoding.
    """
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


def build_authorization_url(
    client_key: str,
    scopes: list[str],
    redirect_uri: str,
    state: str,
    code_challenge: str | None = None,
    code_challenge_method: str = "S256",
) -> str:
    """Build TikTok OAuth 2.0 authorization URL with optional PKCE support."""
    params = {
        "client_key": client_key,
        "scope": ",".join(scopes),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = code_challenge_method
    return f"{TIKTOK_AUTH_URL}?{urllib.parse.urlencode(params)}"


def resolve_tiktok_credentials(
    client_name: str | None = None,
) -> dict[str, str] | None:
    """Resolve TikTok credentials following client-suffix and global fallback."""
    suffix = f"_{client_name.upper()}" if client_name else ""

    if suffix:
        ck = os.environ.get(f"TIKTOK_ORGANIC_CLIENT_KEY{suffix}")
        cs = os.environ.get(f"TIKTOK_ORGANIC_CLIENT_SECRET{suffix}")
        if ck and cs:
            return {"client_key": ck, "client_secret": cs}

    ck = os.environ.get("TIKTOK_ORGANIC_CLIENT_KEY")
    cs = os.environ.get("TIKTOK_ORGANIC_CLIENT_SECRET")
    if ck and cs:
        return {"client_key": ck, "client_secret": cs}

    return None


def update_client_env(
    env_path: Path | str,
    client_name: str | None,
    client_key: str,
    client_secret: str,
    refresh_token: str,
) -> None:
    """Save or update TikTok OAuth credentials in .env file with 0600 permissions."""
    suffix = f"_{client_name.upper()}" if client_name else ""
    update_env_values(
        env_path,
        {
            f"TIKTOK_ORGANIC_CLIENT_KEY{suffix}": client_key,
            f"TIKTOK_ORGANIC_CLIENT_SECRET{suffix}": client_secret,
            f"TIKTOK_ORGANIC_REFRESH_TOKEN{suffix}": refresh_token,
        },
    )


class _TikTokOAuthRedirectHandler(BaseHTTPRequestHandler):
    """Loopback callback handler capturing the TikTok authorization code."""

    def do_GET(self) -> None:  # noqa: N802
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if "error" in params:
            self.server.auth_error = params["error"][0]  # type: ignore[attr-defined]
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization failed or was denied.")
            return

        expected_state = getattr(self.server, "expected_state", None)
        if isinstance(expected_state, str) and expected_state:
            incoming_state = params.get("state", [None])[0]
            if incoming_state != expected_state:
                self.server.auth_error = "state_mismatch"  # type: ignore[attr-defined]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Authorization failed: CSRF state mismatch.")
                return

        code = params.get("code", [None])[0]
        self.server.auth_code = code  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = (
            "<html><body style='font-family:sans-serif;text-align:center;padding:40px;'>"
            "<h2>TikTok Authentication successful!</h2>"
            "<p>You can close this browser tab and return to the terminal.</p>"
            "</body></html>"
        )
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, *args: Any) -> None:  # noqa: N802
        pass


def exchange_code_for_tokens(
    client_key: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Exchange authorization code for access and refresh tokens."""
    data = {
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        response = requests.post(TIKTOK_TOKEN_URL, headers=headers, data=data, timeout=timeout)
        payload = response.json()
    except Exception as exc:
        raise TikTokAuthError(f"Network error while exchanging TikTok auth code: {exc}") from exc

    err = payload.get("error")
    if err and isinstance(err, dict) and err.get("code") not in ("ok", 0, None):
        msg = err.get("message", "unknown error")
        raise TikTokAuthError(f"TikTok token exchange failed: {msg}")
    if err and isinstance(err, str):
        desc = payload.get("error_description", "")
        raise TikTokAuthError(f"TikTok token exchange failed: {err} — {desc}")

    token_data = payload.get("data", payload)
    if not isinstance(token_data, dict) or "refresh_token" not in token_data:
        raise TikTokAuthError("Missing refresh_token in TikTok token exchange response")

    return token_data


def run_tiktok_auth_wizard(
    client_name: str | None = None,
    client_key: str | None = None,
    client_secret: str | None = None,
    env_path: str | Path = ".env",
    interactive: bool = True,
    port: int = 8086,
    no_browser: bool = False,
) -> int:
    """Run interactive TikTok OAuth authentication wizard."""
    print("=" * 65)
    print("      TikTok Organic OAuth Token Renewal Wizard")
    print("=" * 65)
    target_label = f"client '{client_name}'" if client_name else "global agency"
    print(f"Configuring for: {target_label}")
    print(f"Requested scopes: {', '.join(DEFAULT_SCOPES)}")
    print("=" * 65)

    existing_creds = resolve_tiktok_credentials(client_name=client_name)
    resolved_key = client_key or (existing_creds["client_key"] if existing_creds else "")
    resolved_secret = client_secret or (existing_creds["client_secret"] if existing_creds else "")

    if resolved_key and interactive and not client_key:
        print(f"\nDetected TikTok Client Key: {resolved_key[:8]}...")
        use_existing = input("Use existing Client Key & Secret? [Y/n]: ").strip().lower()
        if use_existing not in ("", "y", "yes"):
            resolved_key = ""
            resolved_secret = ""

    while not resolved_key:
        if not interactive:
            print("[ERROR] Missing required --client-key in non-interactive mode.")
            return 1
        resolved_key = input("\nEnter TikTok Client Key: ").strip()

    while not resolved_secret:
        if not interactive:
            print("[ERROR] Missing required --client-secret in non-interactive mode.")
            return 1
        resolved_secret = getpass.getpass("Enter TikTok Client Secret: ").strip()

    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    redirect_uri = f"http://localhost:{port}/"
    auth_url = build_authorization_url(
        resolved_key,
        DEFAULT_SCOPES,
        redirect_uri,
        state=state,
        code_challenge=code_challenge,
    )

    try:
        server = HTTPServer(("localhost", port), _TikTokOAuthRedirectHandler)
    except OSError as exc:
        print(f"[ERROR] Cannot bind to localhost:{port}: {exc}")
        return 1

    server.auth_code = None  # type: ignore[attr-defined]
    server.auth_error = None  # type: ignore[attr-defined]
    server.expected_state = state  # type: ignore[attr-defined]

    print("\n==> [1/2] Opening browser for TikTok OAuth consent...")
    print(f"URL: {auth_url}\n")
    if not no_browser:
        webbrowser.open(auth_url)
    else:
        print("[INFO] Headless mode: Copy the URL above and open it in your browser.")
        print(f"[TIP] If on a remote server/SSH: ssh -L {port}:localhost:{port} user@server\n")

    print(f"Waiting for authorization callback on {redirect_uri}...")
    server.handle_request()

    auth_err = getattr(server, "auth_error", None)
    if auth_err:
        print(f"\n[ERROR] Authorization error: {auth_err}")
        return 1

    auth_code = getattr(server, "auth_code", None)
    if not auth_code:
        print("\n[ERROR] Failed to obtain authorization code.")
        return 1

    print("\n==> [2/2] Exchanging authorization code for tokens...")
    try:
        tokens = exchange_code_for_tokens(
            client_key=resolved_key,
            client_secret=resolved_secret,
            code=auth_code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )
    except TikTokAuthError as exc:
        print(f"\n[ERROR] {exc}")
        return 1

    refresh_token = str(tokens["refresh_token"])
    open_id = tokens.get("open_id")

    print("\n" + "=" * 65)
    print("SUCCESS: Received TikTok OAuth Credentials")
    if open_id:
        print(f"Open ID: {open_id}")
    print("=" * 65)
    print(f"Refresh Token: {mask_secret(refresh_token)}")

    target_env = Path(env_path)
    update_client_env(
        env_path=target_env,
        client_name=client_name,
        client_key=resolved_key,
        client_secret=resolved_secret,
        refresh_token=refresh_token,
    )
    print(f"\n[OK] Updated credentials in {target_env}")
    return 0
