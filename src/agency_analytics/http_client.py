"""Centralized HTTP and Graph API client with resilient retries and fail-fast tokens.

Unifies duplicate retry loops, rate-limit backoff logic, and vendor error handling
across connectors (Meta Ads, Facebook Pages, Instagram Business).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from dlt.sources.helpers import requests

logger = logging.getLogger("agency_analytics.http_client")

RATE_LIMIT_CODES = (4, 17, 80000, 80004, 613)


class TokenExpiredError(Exception):
    """Raised when an API access token has expired and requires immediate renewal."""

    pass


def meta_graph_request(
    method: str,
    url: str,
    platform_name: str = "Meta",
    max_retries: int = 5,
    backoff_base: float = 2.0,
    backoff_max: float = 60.0,
    **kwargs: Any,
) -> Any:
    """Execute an HTTP request against the Meta Graph API with exponential backoff.

    Args:
        method: HTTP method (GET, POST, etc.)
        url: Request URL
        platform_name: Label used in log and error messages (e.g. Meta, Facebook, Instagram)
        max_retries: Maximum number of retry attempts
        backoff_base: Initial backoff multiplier in seconds
        backoff_max: Maximum backoff ceiling in seconds
        **kwargs: Additional arguments passed directly to requests.request()

    Returns:
        Parsed JSON response (dict or list).

    Raises:
        TokenExpiredError: When error code 190 (token expired/invalid) is encountered.
        Exception: On unrecoverable API errors or exhausted retries.
    """
    for attempt in range(max_retries + 1):
        try:
            response = requests.request(method, url, **kwargs)
            data = response.json()
        except requests.HTTPError as err:
            status = getattr(err.response, "status_code", 0)
            try:
                err_data = err.response.json() if err.response is not None else {}
                err_code = err_data.get("error", {}).get("code", 0)
                err_msg = err_data.get("error", {}).get("message", str(err))
            except Exception:
                err_code = 0
                err_msg = str(err)

            logger.warning("[%s] HTTP %d: %s", platform_name, status, err_msg)

            # Fast fail on expired or invalid token
            if err_code == 190:
                raise TokenExpiredError(
                    f"[{platform_name}] Invalid or expired token. "
                    "Renew credentials in .env and redeploy."
                ) from err

            # Rate limit retry
            if status == 429 or err_code in RATE_LIMIT_CODES:
                if attempt < max_retries:
                    wait = min(backoff_base * (2**attempt), backoff_max)
                    logger.info(
                        "[%s] Rate limited (attempt %d/%d). Retrying in %.1fs...",
                        platform_name,
                        attempt + 1,
                        max_retries,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                raise Exception(
                    f"[{platform_name}] API rate limit exceeded after "
                    f"{max_retries} retries: {err_msg}"
                ) from err

            raise Exception(f"[{platform_name}] HTTP error ({status}): {err_msg}") from err

        except requests.RequestException as err:
            logger.warning(
                "[%s] Request exception on attempt %d/%d: %s",
                platform_name,
                attempt + 1,
                max_retries,
                err,
            )
            if attempt < max_retries:
                wait = min(backoff_base * (2**attempt), backoff_max)
                time.sleep(wait)
                continue
            raise Exception(
                f"[{platform_name}] Network request failed after {max_retries} retries: {err}"
            ) from err

        # Check inline error payload (HTTP 200 with error body)
        if isinstance(data, dict) and "error" in data:
            err_code = data["error"].get("code", 0)
            err_msg = data["error"].get("message", "Unknown error")

            if err_code == 190:
                raise TokenExpiredError(f"[{platform_name}] Invalid or expired token: {err_msg}")

            if err_code in RATE_LIMIT_CODES:
                if attempt < max_retries:
                    wait = min(backoff_base * (2**attempt), backoff_max)
                    time.sleep(wait)
                    continue
                raise Exception(
                    f"[{platform_name}] API rate limit exceeded after "
                    f"{max_retries} retries: {err_msg}"
                )

            raise Exception(f"[{platform_name}] API error ({err_code}): {err_msg}")

        return data
