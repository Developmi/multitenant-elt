"""Unified Google OAuth 2.0 authentication and token management engine.

Provides memory-cached token auto-refresh for Google services (YouTube, GTM, Google Ads),
hierarchical multi-tenant credential resolution, and an interactive OAuth consent wizard.
"""

from __future__ import annotations

import getpass
import os
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import dotenv
from dlt.sources.helpers import requests

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

SERVICES_SCOPES: dict[str, list[str]] = {
    "youtube": [
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ],
    "gtm": [
        "https://www.googleapis.com/auth/tagmanager.readonly",
    ],
    "ads": [
        "https://www.googleapis.com/auth/adwords",
    ],
}


class GoogleAuthError(Exception):
    """Raised when Google OAuth operations fail."""


class GoogleTokenManager:
    """Cached OAuth access token obtained from a refresh token.

    Maintains in-memory access token cache with proactive 5-minute pre-expiration
    refresh to prevent mid-pipeline authentication errors.
    """

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._credentials: Any | None = None

    @property
    def credentials(self) -> Any:
        """Return the official google.oauth2.credentials.Credentials instance."""
        from google.oauth2.credentials import Credentials

        if self._credentials is None:
            self._credentials = Credentials(
                token=self._access_token,
                refresh_token=self._refresh_token,
                token_uri=GOOGLE_TOKEN_URL,
                client_id=self._client_id,
                client_secret=self._client_secret,
            )
        return self._credentials

    def get_token(self) -> str:
        """Return a valid access token, auto-refreshing if expired or uninitialized."""
        now = time.time()
        # Refresh 5 minutes (300 seconds) prior to expiration
        if self._access_token is not None and now < (self._expires_at - 300):
            return self._access_token

        payload = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }

        try:
            response = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=20)
        except Exception as exc:
            raise GoogleAuthError(
                f"Network error while refreshing Google OAuth token: {exc}"
            ) from exc

        try:
            data = response.json()
        except Exception as exc:
            raise GoogleAuthError(
                f"Failed to parse Google OAuth token response (HTTP {response.status_code})"
            ) from exc

        status = getattr(response, "status_code", 200)
        if (isinstance(status, int) and status != 200) or "access_token" not in data:
            error = data.get("error", "unknown_error")
            desc = data.get("error_description", str(data))
            raise GoogleAuthError(f"Google token refresh failed ({error}): {desc}")

        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        self._expires_at = now + expires_in
        return self._access_token

    def invalidate(self) -> None:
        """Clear cached access token to force refresh on next call."""
        self._access_token = None
        self._expires_at = 0.0

    def _refresh(self) -> str:
        """Force immediate refresh and return fresh access token."""
        self.invalidate()
        return self.get_token()


def resolve_google_credentials(
    client_name: str | None = None,
    service: str = "all",
) -> dict[str, str] | None:
    """Resolve Google OAuth credentials following hierarchical fallback.

    1. Client-specific overrides: GOOGLE_OAUTH_*_{CLIENT}
    2. Global agency credentials: GOOGLE_OAUTH_*
    3. Legacy service-specific keys (e.g. YOUTUBE_OAUTH_*)
    """
    client_suffix = f"_{client_name.upper()}" if client_name else ""

    # 1. Client-specific generic keys
    if client_suffix:
        cid = os.environ.get(f"GOOGLE_OAUTH_CLIENT_ID{client_suffix}")
        csec = os.environ.get(f"GOOGLE_OAUTH_CLIENT_SECRET{client_suffix}")
        rtoken = os.environ.get(f"GOOGLE_OAUTH_REFRESH_TOKEN{client_suffix}")
        if cid and csec and rtoken:
            return {"client_id": cid, "client_secret": csec, "refresh_token": rtoken}

    # 2. Global generic keys
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    csec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    rtoken = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")
    if cid and csec and rtoken:
        return {"client_id": cid, "client_secret": csec, "refresh_token": rtoken}

    # 3. Legacy service-specific keys (e.g. YouTube)
    if service == "youtube":
        if client_suffix:
            cid = os.environ.get(f"YOUTUBE_OAUTH_CLIENT_ID{client_suffix}")
            csec = os.environ.get(f"YOUTUBE_OAUTH_CLIENT_SECRET{client_suffix}")
            rtoken = os.environ.get(f"YOUTUBE_OAUTH_REFRESH_TOKEN{client_suffix}")
            if cid and csec and rtoken:
                return {"client_id": cid, "client_secret": csec, "refresh_token": rtoken}

        cid = os.environ.get("YOUTUBE_OAUTH_CLIENT_ID")
        csec = os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET")
        rtoken = os.environ.get("YOUTUBE_OAUTH_REFRESH_TOKEN")
        if cid and csec and rtoken:
            return {"client_id": cid, "client_secret": csec, "refresh_token": rtoken}

    return None


def get_scopes_for_services(services: list[str]) -> list[str]:
    """Return aggregated, deduplicated OAuth scopes for requested service aliases."""
    if "all" in services:
        all_scopes: list[str] = []
        for scope_list in SERVICES_SCOPES.values():
            for scope in scope_list:
                if scope not in all_scopes:
                    all_scopes.append(scope)
        return all_scopes

    resolved: list[str] = []
    for s in services:
        key = s.lower().strip()
        if key in SERVICES_SCOPES:
            for scope in SERVICES_SCOPES[key]:
                if scope not in resolved:
                    resolved.append(scope)
    return resolved


def build_authorization_url(client_id: str, scopes: list[str], redirect_uri: str) -> str:
    """Build Google OAuth 2.0 authorization URL with offline access and explicit consent."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def update_client_env(
    env_path: Path,
    client_name: str | None,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> None:
    """Save or update Google OAuth credentials in .env file cleanly."""
    if not env_path.exists():
        env_path.touch()
    suffix = f"_{client_name.upper()}" if client_name else ""
    dotenv.set_key(str(env_path), f"GOOGLE_OAUTH_CLIENT_ID{suffix}", client_id, quote_mode="never")
    dotenv.set_key(
        str(env_path), f"GOOGLE_OAUTH_CLIENT_SECRET{suffix}", client_secret, quote_mode="never"
    )
    dotenv.set_key(
        str(env_path), f"GOOGLE_OAUTH_REFRESH_TOKEN{suffix}", refresh_token, quote_mode="never"
    )


class _OAuthRedirectHandler(BaseHTTPRequestHandler):
    """Loopback callback handler capturing the Google authorization code."""

    def do_GET(self) -> None:  # noqa: N802
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if "error" in params:
            self.server.auth_error = params["error"][0]  # type: ignore[attr-defined]
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization failed or was denied.")
            return

        code = params.get("code", [None])[0]
        self.server.auth_code = code  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = (
            "<html><body style='font-family:sans-serif;text-align:center;padding:40px;'>"
            "<h2>Authentication successful!</h2>"
            "<p>You can close this browser tab and return to the terminal.</p>"
            "</body></html>"
        )
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, *args: Any) -> None:  # noqa: N802
        pass


def run_google_auth_wizard(
    client_name: str | None = None,
    services: list[str] | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    env_path: str | Path = ".env",
    interactive: bool = True,
    port: int = 8085,
    no_browser: bool = False,
) -> int:
    """Run interactive Google OAuth authentication wizard."""
    services = services or ["all"]
    scopes = get_scopes_for_services(services)
    if not scopes:
        print("[ERROR] No valid services specified. Use: all, youtube, gtm, or ads.")
        return 1

    print("=" * 65)
    print("      Google Unified OAuth Token Renewal Wizard")
    print("=" * 65)
    target_label = f"client '{client_name}'" if client_name else "global agency"
    print(f"Configuring for: {target_label}")
    print(f"Requested services: {', '.join(services)}")
    print("=" * 65)

    # 1. Resolve or prompt client_id & client_secret
    existing_creds = resolve_google_credentials(client_name=client_name)
    resolved_id = client_id or (existing_creds["client_id"] if existing_creds else "")
    resolved_secret = client_secret or (existing_creds["client_secret"] if existing_creds else "")

    if resolved_id and interactive and not client_id:
        print(f"\nDetected Google OAuth Client ID: {resolved_id[:12]}...")
        use_existing = input("Use existing Client ID & Secret? [Y/n]: ").strip().lower()
        if use_existing not in ("", "y", "yes"):
            resolved_id = ""
            resolved_secret = ""

    while not resolved_id:
        if not interactive:
            print("[ERROR] Missing required --client-id in non-interactive mode.")
            return 1
        resolved_id = input("\nEnter Google OAuth Client ID: ").strip()

    while not resolved_secret:
        if not interactive:
            print("[ERROR] Missing required --client-secret in non-interactive mode.")
            return 1
        resolved_secret = getpass.getpass("Enter Google OAuth Client Secret: ").strip()

    redirect_uri = f"http://localhost:{port}"
    auth_url = build_authorization_url(resolved_id, scopes, redirect_uri)

    # 2. Start loopback server and trigger consent flow
    server = HTTPServer(("localhost", port), _OAuthRedirectHandler)
    server.auth_code = None  # type: ignore[attr-defined]
    server.auth_error = None  # type: ignore[attr-defined]

    print("\n==> [1/2] Opening browser for Google OAuth consent...")
    print(f"URL: {auth_url}\n")
    if not no_browser:
        webbrowser.open(auth_url)

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

    # 3. Exchange auth code for tokens
    print("\n==> [2/2] Exchanging authorization code for permanent Refresh Token...")
    payload = {
        "client_id": resolved_id,
        "client_secret": resolved_secret,
        "code": auth_code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }

    try:
        resp = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=20)
        token_data = resp.json()
    except Exception as exc:
        print(f"[ERROR] Token exchange request failed: {exc}")
        return 1

    if resp.status_code != 200 or "refresh_token" not in token_data:
        err = token_data.get("error", "unknown_error")
        desc = token_data.get("error_description", str(token_data))
        print(f"\n[ERROR] Token exchange failed ({err}): {desc}")
        if "invalid_grant" in err:
            print("[HINT] The authorization code expired or has already been used.")
        return 1

    refresh_token = token_data["refresh_token"]

    print("\n" + "=" * 65)
    print("SUCCESS: Received Permanent Google OAuth Refresh Token")
    print("=" * 65)
    masked_rt = refresh_token[:8] + "..." + refresh_token[-6:]
    print(f"Refresh Token: {masked_rt}")

    target_env = Path(env_path)
    update_client_env(
        env_path=target_env,
        client_name=client_name,
        client_id=resolved_id,
        client_secret=resolved_secret,
        refresh_token=refresh_token,
    )
    print(f"\n[OK] Updated credentials in {target_env}")
    return 0
