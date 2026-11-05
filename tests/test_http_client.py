"""Unit tests for the centralized HTTP and Meta Graph API client (agency_analytics.http_client).

Tests written RED first:
- Successful GET and POST requests
- Automatic exponential backoff on HTTP 429 and Graph API rate limit error codes
- Immediate fail-fast on token expiration (error code 190)
- Network exception retry and max-retries exceeded raising
"""

from unittest.mock import MagicMock, patch

import pytest
from dlt.sources.helpers import requests

from agency_analytics.http_client import (
    TokenExpiredError,
    meta_graph_request,
)


def test_meta_graph_request_success():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": [{"id": "123"}]}
    mock_resp.status_code = 200

    with patch.object(requests, "request", return_value=mock_resp) as mock_req:
        result = meta_graph_request("GET", "https://graph.facebook.com/v25.0/me", params={"a": 1})
        assert result == {"data": [{"id": "123"}]}
        mock_req.assert_called_once_with(
            "GET", "https://graph.facebook.com/v25.0/me", params={"a": 1}
        )


def test_meta_graph_request_rate_limit_retry_and_succeed():
    err_resp = MagicMock()
    err_resp.status_code = 429
    err_resp.json.return_value = {"error": {"code": 4, "message": "Rate limit hit"}}
    http_err = requests.HTTPError(response=err_resp)

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"success": True}

    with (
        patch.object(requests, "request", side_effect=[http_err, ok_resp]),
        patch("time.sleep") as mock_sleep,
    ):
        result = meta_graph_request(
            "GET",
            "https://graph.facebook.com/v25.0/me",
            max_retries=2,
            backoff_base=1,
            platform_name="Instagram",
        )
        assert result == {"success": True}
        mock_sleep.assert_called_once_with(1)  # 1 * 2**0


def test_meta_graph_request_token_expired_fails_fast():
    err_resp = MagicMock()
    err_resp.status_code = 400
    err_resp.json.return_value = {
        "error": {"code": 190, "message": "Error validating access token"}
    }
    http_err = requests.HTTPError(response=err_resp)

    with (
        patch.object(requests, "request", side_effect=http_err),
        patch("time.sleep") as mock_sleep,
    ):
        with pytest.raises(TokenExpiredError, match="Invalid or expired token"):
            meta_graph_request(
                "GET", "https://graph.facebook.com/v25.0/me", platform_name="Facebook"
            )
        mock_sleep.assert_not_called()


def test_meta_graph_request_max_retries_exceeded():
    err_resp = MagicMock()
    err_resp.status_code = 429
    err_resp.json.return_value = {"error": {"code": 17, "message": "User request limit reached"}}
    http_err = requests.HTTPError(response=err_resp)

    with (
        patch.object(requests, "request", side_effect=http_err),
        patch("time.sleep"),
    ):
        with pytest.raises(Exception, match="rate limit exceeded"):
            meta_graph_request(
                "GET",
                "https://graph.facebook.com/v25.0/me",
                max_retries=2,
                backoff_base=1,
            )


def test_meta_graph_request_network_exception_retry():
    net_err = requests.RequestException("Connection reset")
    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"status": "recovered"}

    with (
        patch.object(requests, "request", side_effect=[net_err, ok_resp]),
        patch("time.sleep"),
    ):
        result = meta_graph_request(
            "GET",
            "https://graph.facebook.com/v25.0/me",
            max_retries=2,
            backoff_base=1,
        )
        assert result == {"status": "recovered"}


def test_meta_graph_request_inline_error_token_expired():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"error": {"code": 190, "message": "Expired token"}}

    with patch.object(requests, "request", return_value=mock_resp):
        with pytest.raises(TokenExpiredError, match="Invalid or expired token"):
            meta_graph_request("GET", "https://graph.facebook.com/v25.0/me")


def test_meta_graph_request_inline_error_rate_limit_retry():
    rate_resp = MagicMock()
    rate_resp.status_code = 200
    rate_resp.json.return_value = {"error": {"code": 4, "message": "Rate limited"}}

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"data": "ok"}

    with (
        patch.object(requests, "request", side_effect=[rate_resp, ok_resp]),
        patch("time.sleep"),
    ):
        result = meta_graph_request("GET", "https://graph.facebook.com/v25.0/me", max_retries=2)
        assert result == {"data": "ok"}


def test_meta_graph_request_inline_generic_error():
    err_resp = MagicMock()
    err_resp.status_code = 200
    err_resp.json.return_value = {"error": {"code": 500, "message": "Internal error"}}

    with patch.object(requests, "request", return_value=err_resp):
        with pytest.raises(Exception, match="API error"):
            meta_graph_request("GET", "https://graph.facebook.com/v25.0/me")


def test_meta_graph_request_generic_http_error():
    err_resp = MagicMock()
    err_resp.status_code = 500
    err_resp.json.side_effect = ValueError("Invalid JSON")
    http_err = requests.HTTPError(response=err_resp)

    with patch.object(requests, "request", side_effect=http_err):
        with pytest.raises(Exception, match="HTTP error"):
            meta_graph_request("GET", "https://graph.facebook.com/v25.0/me")
