"""Meta Graph API authentication and token renewal wizard.

Provides utility functions and an interactive CLI workflow to inspect Meta
user tokens, exchange short-lived tokens for 60-day long-lived tokens, resolve
permanent Page Access Tokens, and inspect linked Instagram accounts.
"""

from __future__ import annotations

import dataclasses
import getpass
import os
import sys
from pathlib import Path

import dotenv
import requests

GRAPH_API_BASE = "https://graph.facebook.com/v25.0"
DEFAULT_TIMEOUT_SECONDS = 15


class MetaAuthError(Exception):
    """Raised when an operation against the Meta Graph API fails."""


@dataclasses.dataclass(frozen=True)
class MetaTokenInfo:
    """Represents inspected metadata for a Meta Access Token."""

    is_valid: bool
    user_id: str
    app_id: str
    scopes: list[str]
    expires_at: int | None = None


@dataclasses.dataclass(frozen=True)
class MetaPageAccount:
    """Represents a Facebook Page and its linked Instagram Business account."""

    page_id: str
    page_name: str
    access_token: str
    instagram_id: str | None = None
    instagram_username: str | None = None


def debug_user_token(
    token: str,
    session: requests.Session | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> MetaTokenInfo:
    """Inspect and validate a Meta user access token.

    Args:
        token: User access token string to debug.
        session: Optional pre-configured requests session.
        timeout: Request timeout in seconds.

    Returns:
        MetaTokenInfo dataclass containing token validity and scopes.

    Raises:
        MetaAuthError: If the token is invalid or the API request fails.
    """
    url = f"{GRAPH_API_BASE}/debug_token"
    params = {"input_token": token, "access_token": token}
    client = session or requests

    try:
        response = client.get(url, params=params, timeout=timeout)
        payload = response.json()
    except Exception as exc:
        raise MetaAuthError(f"Network error while inspecting token: {exc}") from exc

    if "error" in payload:
        err = payload["error"]
        error_msg = err.get("message", "Unknown token error")
        error_code = err.get("code")
        error_subcode = err.get("error_subcode")
        details = []
        if error_code:
            details.append(f"code: {error_code}")
        if error_subcode:
            details.append(f"subcode: {error_subcode}")
        details_str = f" ({', '.join(details)})" if details else ""

        hint = ""
        if error_code == 190 and error_subcode == 460:
            hint = (
                "\n[HINT] The user session was invalidated (password change or security reset). "
                "Generate a fresh User Access Token in developers.facebook.com/tools/explorer."
            )
        elif error_code == 190:
            hint = (
                "\n[HINT] Token has expired or is invalid. "
                "Generate a new token in Graph API Explorer."
            )

        raise MetaAuthError(f"Token validation failed: {error_msg}{details_str}{hint}")

    data = payload.get("data", {})
    return MetaTokenInfo(
        is_valid=bool(data.get("is_valid", False)),
        user_id=str(data.get("user_id", "")),
        app_id=str(data.get("app_id", "")),
        scopes=list(data.get("scopes", [])),
        expires_at=data.get("expires_at"),
    )


def exchange_long_lived_token(
    app_id: str,
    app_secret: str,
    short_token: str,
    session: requests.Session | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Exchange a short-lived user token for a 60-day long-lived user token.

    Args:
        app_id: Meta Application ID.
        app_secret: Meta Application Secret.
        short_token: Short-lived user token.
        session: Optional pre-configured requests session.
        timeout: Request timeout in seconds.

    Returns:
        The 60-day long-lived user access token string.

    Raises:
        MetaAuthError: If the exchange fails.
    """
    url = f"{GRAPH_API_BASE}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token,
    }
    client = session or requests

    try:
        response = client.get(url, params=params, timeout=timeout)
        payload = response.json()
    except Exception as exc:
        raise MetaAuthError(f"Network error during token exchange: {exc}") from exc

    if "error" in payload:
        err = payload["error"]
        error_msg = err.get("message", "Token exchange failed")
        error_code = err.get("code")
        error_subcode = err.get("error_subcode")
        details = []
        if error_code:
            details.append(f"code: {error_code}")
        if error_subcode:
            details.append(f"subcode: {error_subcode}")
        details_str = f" ({', '.join(details)})" if details else ""

        hint = ""
        if "client secret" in error_msg.lower():
            hint = (
                "\n[HINT] Ensure you entered your Meta App Secret (found under "
                "Meta App Settings > Basic in developers.facebook.com), NOT an access token."
            )
        elif "app id" in error_msg.lower():
            hint = (
                "\n[HINT] Verify that the Meta App ID matches the application "
                "created in developers.facebook.com."
            )

        raise MetaAuthError(f"Exchange failed: {error_msg}{details_str}{hint}")

    access_token = payload.get("access_token")
    if not access_token:
        raise MetaAuthError("Missing access_token in exchange response")

    return str(access_token)


def list_page_accounts(
    user_token: str,
    session: requests.Session | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[MetaPageAccount]:
    """Retrieve all Facebook Pages and linked Instagram accounts for the token user.

    Args:
        user_token: Valid long-lived (or user) access token.
        session: Optional pre-configured requests session.
        timeout: Request timeout in seconds.

    Returns:
        A list of MetaPageAccount instances with page and instagram details.

    Raises:
        MetaAuthError: If querying accounts fails.
    """
    url = f"{GRAPH_API_BASE}/me/accounts"
    params = {"access_token": user_token}
    client = session or requests

    try:
        response = client.get(url, params=params, timeout=timeout)
        payload = response.json()
    except Exception as exc:
        raise MetaAuthError(f"Network error fetching accounts: {exc}") from exc

    if "error" in payload:
        error_msg = payload["error"].get("message", "Failed to retrieve accounts")
        raise MetaAuthError(f"Accounts retrieval failed: {error_msg}")

    pages: list[MetaPageAccount] = []
    for item in payload.get("data", []):
        page_id = str(item.get("id"))
        page_name = str(item.get("name", "Unknown Page"))
        page_token = str(item.get("access_token", ""))

        instagram_id = None
        instagram_username = None

        # Inspect linked Instagram Business Account using page token
        try:
            ig_url = f"{GRAPH_API_BASE}/{page_id}"
            ig_params = {
                "fields": "instagram_business_account{id,username}",
                "access_token": page_token,
            }
            ig_resp = client.get(ig_url, params=ig_params, timeout=timeout)
            ig_data = ig_resp.json()
            ig_account = ig_data.get("instagram_business_account")
            if ig_account:
                instagram_id = str(ig_account.get("id"))
                instagram_username = str(ig_account.get("username"))
        except Exception:
            # Non-fatal: page might not have a linked Instagram account
            pass

        pages.append(
            MetaPageAccount(
                page_id=page_id,
                page_name=page_name,
                access_token=page_token,
                instagram_id=instagram_id,
                instagram_username=instagram_username,
            )
        )

    return pages


def update_client_env(env_path: Path | str, client_id: str, page_token: str) -> bool:
    """Safely update FACEBOOK_ACCESS_TOKEN and INSTAGRAM_ACCESS_TOKEN in .env.

    Preserves existing comments, whitespace, and unmodified keys.

    Args:
        env_path: Path to the target .env file.
        client_id: Client identifier (e.g. 'client_example').
        page_token: New permanent Page Access Token.

    Returns:
        True if the file was updated successfully.
    """
    path = Path(env_path)
    if not path.exists():
        path.touch()
    prefix = client_id.upper()
    dotenv.set_key(str(path), f"FACEBOOK_ACCESS_TOKEN_{prefix}", page_token, quote_mode="never")
    dotenv.set_key(str(path), f"INSTAGRAM_ACCESS_TOKEN_{prefix}", page_token, quote_mode="never")
    return True


def run_meta_auth_wizard(
    client_id: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    token: str | None = None,
    env_path: str = ".env",
    interactive: bool = True,
) -> int:
    """Run the step-by-step interactive Meta token renewal wizard.

    Args:
        client_id: Optional client identifier (e.g. 'client_example').
        app_id: Meta App ID.
        app_secret: Meta App Secret.
        token: Short-lived user access token.
        env_path: Path to .env file for writing updates.
        interactive: Whether to prompt the user via CLI.

    Returns:
        0 on success, non-zero on failure.
    """
    print("\n" + "=" * 65)
    print("      Meta Graph API Token Renewal Wizard")
    print("=" * 65 + "\n")

    # Step 1: App Credentials
    if not app_id:
        default_app_id = os.environ.get("META_APP_ID", "")
        if interactive:
            prompt = (
                f"Enter Meta App ID [{default_app_id}]: "
                if default_app_id
                else "Enter Meta App ID: "
            )
            user_val = input(prompt).strip()
            app_id = user_val if user_val else default_app_id
        else:
            app_id = default_app_id

    if not app_secret:
        if interactive:
            app_secret = getpass.getpass("Enter Meta App Secret: ").strip()
        else:
            app_secret = os.environ.get("META_APP_SECRET", "").strip()

    if not app_id or not app_secret:
        print("[ERROR] Meta App ID and App Secret are required.", file=sys.stderr)
        return 1

    # Step 2: User Access Token
    if not token:
        if interactive:
            print("\nPaste your short-lived User Access Token from Graph API Explorer:")
            token = getpass.getpass("Token (hidden input): ").strip()
        else:
            print("[ERROR] User token is required in non-interactive mode.", file=sys.stderr)
            return 1

    if not token:
        print("[ERROR] Token cannot be empty.", file=sys.stderr)
        return 1

    # Validate token
    print("\n==> [1/3] Inspecting User Token with debug_token...")
    try:
        info = debug_user_token(token)
        print(f"    [OK] Token valid for User ID: {info.user_id}")
        required_scopes = {"pages_show_list", "pages_read_engagement"}
        missing_scopes = required_scopes - set(info.scopes)
        if missing_scopes:
            print(
                f"    [WARN] Token is missing recommended scopes: {', '.join(missing_scopes)}",
                file=sys.stderr,
            )
    except MetaAuthError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    # Step 3: Exchange for Long-Lived Token
    print("\n==> [2/3] Exchanging for 60-day Long-Lived User Token...")
    try:
        long_lived_token = exchange_long_lived_token(app_id, app_secret, token)
        print("    [OK] Received 60-day Long-Lived User Token.")
    except MetaAuthError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    # Step 4: Fetch Managed Pages
    print("\n==> [3/3] Fetching managed Facebook Pages & Instagram accounts...")
    try:
        pages = list_page_accounts(long_lived_token)
    except MetaAuthError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if not pages:
        print("[ERROR] No Facebook Pages found for this user account.", file=sys.stderr)
        return 1

    selected_page: MetaPageAccount
    if len(pages) == 1:
        selected_page = pages[0]
        print(f"\nFound 1 Page: '{selected_page.page_name}' (ID: {selected_page.page_id})")
        if selected_page.instagram_username:
            ig_user = selected_page.instagram_username
            ig_id = selected_page.instagram_id
            print(f"Linked Instagram: @{ig_user} (ID: {ig_id})")
    else:
        print(f"\nAvailable Pages ({len(pages)}):")
        for idx, p in enumerate(pages, start=1):
            ig_label = f" | IG: @{p.instagram_username}" if p.instagram_username else ""
            print(f"  [{idx}] {p.page_name} (ID: {p.page_id}){ig_label}")

        choice = 1
        if interactive:
            while True:
                val = input(f"\nSelect Page number [1-{len(pages)}] (default 1): ").strip()
                if not val:
                    choice = 1
                    break
                if val.isdigit() and 1 <= int(val) <= len(pages):
                    choice = int(val)
                    break
                print("Invalid selection. Try again.")
        selected_page = pages[choice - 1]

    # Present Results
    print("\n" + "=" * 65)
    print("SUCCESS: Generated Permanent Page Access Token")
    print(f"Page: {selected_page.page_name} (ID: {selected_page.page_id})")
    if selected_page.instagram_username:
        print(f"Instagram: @{selected_page.instagram_username} (ID: {selected_page.instagram_id})")
    print("=" * 65)
    print(f"\nPAGE ACCESS TOKEN:\n{selected_page.access_token}\n")

    # Update .env
    target_client = client_id
    if interactive and not target_client:
        prompt_client = input(
            "Enter client name to update in .env (e.g. 'client_example', or leave blank to skip): "
        ).strip()
        if prompt_client:
            target_client = prompt_client

    if target_client:
        update_client_env(env_path, target_client, selected_page.access_token)
        print(f"[OK] Successfully updated {env_path} for client '{target_client}':")
        prefix = target_client.upper()
        print(f"     FACEBOOK_ACCESS_TOKEN_{prefix}=<TOKEN>")
        print(f"     INSTAGRAM_ACCESS_TOKEN_{prefix}=<TOKEN>")
    else:
        print("Note: .env file was not updated. Copy and paste the token above into your .env.")

    return 0
