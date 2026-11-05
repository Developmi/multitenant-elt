import copy
from unittest.mock import MagicMock, patch

import pytest

from agency_analytics.google_auth import GoogleTokenManager

GOOGLE_AD_ROW = {
    "adGroupAd": {
        "ad": {"id": "4928103746", "name": "Google Ad Test"},
        "status": "ENABLED",
    },
    "adGroup": {"id": "8573629104"},
    "campaign": {"id": "1298475620"},
    "metrics": {
        "costMicros": 2500000,
        "impressions": 45000,
        "clicks": 2250,
        "ctr": 5.0,
        "averageCpc": 1111,
        "conversions": 15.5,
        "costPerConversion": 161290,
    },
    "segments": {"date": "2024-06-15"},
}

GOOGLE_CAMPAIGN_ROW = {
    "campaign": {
        "id": "1298475620",
        "name": "Google Campaign Test",
        "status": "ENABLED",
        "advertisingChannelType": "SEARCH",
        "budget": {"amountMicros": 50000000, "type": "DAILY"},
        "startDate": "2024-06-01",
        "endDate": "2024-06-30",
    },
    "metrics": {
        "costMicros": 2500000,
        "impressions": 45000,
        "clicks": 2250,
        "ctr": 5.0,
        "averageCpc": 1111,
        "conversions": 15.5,
        "costPerConversion": 161290,
    },
}


def _mock_search_response(results, next_page_token=None):
    resp = MagicMock()
    body = {"results": results}
    if next_page_token:
        body["nextPageToken"] = next_page_token
    resp.json.return_value = body
    return resp


def _mock_error_response(code, message, **extra):
    resp = MagicMock()
    error = {"code": code, "message": message}
    if extra:
        error.update(extra)
    resp.json.return_value = {"error": error}
    return resp


def _mock_429_response(retry_delay="30s"):
    return _mock_error_response(429, "Rate limited", details=[{"retryDelay": retry_delay}])


@pytest.fixture
def mock_google_api():
    with patch("dlt.sources.helpers.requests.post") as mock_post:
        yield mock_post


@pytest.fixture
def mock_sleep():
    with patch("time.sleep") as mock:
        yield mock


class TestGoogleAdsConnector:
    def test_get_ads_mapping(self, mock_google_api):
        mock_google_api.return_value = _mock_search_response([GOOGLE_AD_ROW])
        from run_google import get_ads

        results = list(get_ads("123-456-7890", access_token="oauth_token"))
        assert len(results) == 1
        r = results[0]
        assert r["ad_id"] == "4928103746"
        assert r["ad_name"] == "Google Ad Test"
        assert r["ad_status"] == "ENABLED"
        assert r["ad_group_id"] == "8573629104"
        assert r["campaign_id"] == "1298475620"
        assert r["spend"] == 2.5
        assert r["impressions"] == 45000
        assert r["clicks"] == 2250
        assert r["ctr"] == 5.0
        assert r["cpc"] == 0.0
        assert r["conversions"] == 15.5
        assert r["cost_per_conversion"] == 0.16
        assert r["date"] == "2024-06-15"

        # Verify no developer-token header is sent (sunset September 2026)
        call_headers = mock_google_api.call_args[1]["headers"]
        assert "developer-token" not in call_headers
        assert call_headers["Authorization"] == "Bearer oauth_token"

    def test_get_campaigns_mapping(self, mock_google_api):
        mock_google_api.return_value = _mock_search_response([GOOGLE_CAMPAIGN_ROW])
        from run_google import get_campaigns

        results = list(get_campaigns("123-456-7890", access_token="oauth_token"))
        assert len(results) == 1
        r = results[0]
        assert r["campaign_id"] == "1298475620"
        assert r["campaign_name"] == "Google Campaign Test"
        assert r["status"] == "ENABLED"
        assert r["channel_type"] == "SEARCH"
        assert r["daily_budget"] == 50.0
        assert r["spend"] == 2.5
        assert r["impressions"] == 45000
        assert r["clicks"] == 2250
        assert r["ctr"] == 5.0
        assert r["cpc"] == 0.0
        assert r["conversions"] == 15.5
        assert r["cost_per_conversion"] == 0.16
        assert r["start_date"] == "2024-06-01"
        assert r["end_date"] == "2024-06-30"
        assert r["date"] == "2024-06-01"

    def test_rate_limit_retry(self, mock_google_api, mock_sleep):
        error = _mock_429_response(retry_delay="1s")
        success = _mock_search_response([GOOGLE_AD_ROW])
        mock_google_api.side_effect = [error, error, error, error, success]
        from run_google import get_ads

        results = list(get_ads("123-456-7890", access_token="oauth_token"))
        assert len(results) == 1
        assert mock_google_api.call_count == 5

    def test_token_expired(self, mock_google_api):
        mock_google_api.return_value = _mock_error_response(401, "Unauthorized")
        from run_google import get_ads

        with pytest.raises(Exception, match="Token expired"):
            list(get_ads("123-456-7890", access_token="oauth_token"))

    def test_token_manager_auto_refresh_on_401(self, mock_google_api):
        """When token_manager is passed and a 401 is encountered, it refreshes and retries."""
        manager = MagicMock(spec=GoogleTokenManager)
        manager.get_token.side_effect = ["expired_token", "fresh_token"]

        err401 = _mock_error_response(401, "Unauthorized")
        success = _mock_search_response([GOOGLE_AD_ROW])
        mock_google_api.side_effect = [err401, success]

        from run_google import get_ads

        results = list(get_ads("123-456-7890", token_manager=manager))
        assert len(results) == 1
        assert mock_google_api.call_count == 2
        # Second call should use the freshly refreshed token
        second_headers = mock_google_api.call_args_list[1][1]["headers"]
        assert second_headers["Authorization"] == "Bearer fresh_token"

    def test_pagination(self, mock_google_api):
        row1 = copy.deepcopy(GOOGLE_AD_ROW)
        row1["adGroupAd"]["ad"]["id"] = "1"
        row2 = copy.deepcopy(GOOGLE_AD_ROW)
        row2["adGroupAd"]["ad"]["id"] = "2"
        page1 = _mock_search_response([row1], next_page_token="abc123")
        page2 = _mock_search_response([row2])
        mock_google_api.side_effect = [page1, page2]
        from run_google import get_ads

        results = list(get_ads("123-456-7890", access_token="oauth_token"))
        assert len(results) == 2
        assert results[0]["ad_id"] == "1"
        assert results[1]["ad_id"] == "2"

    def test_client_not_found(self):
        from run_google import main

        with patch("sys.argv", ["run_google.py", "--client", "nonexistent"]):
            with patch("os.path.exists", return_value=False):
                with pytest.raises(SystemExit) as exc:
                    main()
        assert exc.value.code == 1

    def test_google_request_error_branches(self, mock_sleep):
        from dlt.sources.helpers import requests as dlt_requests
        from run_google import _google_request

        # RequestException retry and raise
        with patch(
            "dlt.sources.helpers.requests.post",
            side_effect=dlt_requests.RequestException("Conn drop"),
        ):
            with pytest.raises(dlt_requests.RequestException):
                _google_request("http://test", {}, {}, max_retries=2)

        # 429 max retries
        with patch(
            "dlt.sources.helpers.requests.post", return_value=_mock_429_response(retry_delay="10s")
        ):
            with pytest.raises(Exception, match="exceeded on rate limit"):
                _google_request("http://test", {}, {}, max_retries=1)

        # 403 access denied
        with patch(
            "dlt.sources.helpers.requests.post", return_value=_mock_error_response(403, "Forbidden")
        ):
            with pytest.raises(Exception, match="Access denied: Forbidden"):
                _google_request("http://test", {}, {}, max_retries=1)

        # generic error
        with patch(
            "dlt.sources.helpers.requests.post",
            return_value=_mock_error_response(500, "Internal Server"),
        ):
            with pytest.raises(Exception, match="API error 500"):
                _google_request("http://test", {}, {}, max_retries=1)

    def test_google_main_branches(self, tmp_path):
        import sys

        from run_google import main

        fake_file = tmp_path / "c.yml"
        fake_file.write_text("active: true\n")

        # 1. Inactive client -> exit(0)
        with patch.object(sys, "argv", ["run_google", "--client", "c"]):
            with patch("os.path.exists", return_value=True):
                with patch(
                    "agency_analytics.client_config.load_client", return_value={"active": False}
                ):
                    with pytest.raises(SystemExit) as exc:
                        main()
                    assert exc.value.code == 0

        # 2. Validation error -> exit(1)
        with patch.object(sys, "argv", ["run_google", "--client", "c"]):
            with patch("os.path.exists", return_value=True):
                with patch(
                    "agency_analytics.client_config.load_client", return_value={"active": True}
                ):
                    with patch(
                        "agency_analytics.client_config.validate_client", return_value=["Err"]
                    ):
                        with pytest.raises(SystemExit) as exc:
                            main()
                        assert exc.value.code == 1

        # 3. Connector disabled -> exit(0)
        client_cfg = {"active": True, "connectors": {"google": {"enabled": False}}}
        with patch.object(sys, "argv", ["run_google", "--client", "c"]):
            with patch("os.path.exists", return_value=True):
                with patch("agency_analytics.client_config.load_client", return_value=client_cfg):
                    with patch("agency_analytics.client_config.validate_client", return_value=[]):
                        with pytest.raises(SystemExit) as exc:
                            main()
                        assert exc.value.code == 0

        # 4. Missing credentials -> exit(1)
        client_cfg_active = {
            "active": True,
            "connectors": {"google": {"enabled": True, "customer_id": "123"}},
        }
        with patch.object(sys, "argv", ["run_google", "--client", "c"]):
            with patch("os.path.exists", return_value=True):
                with patch(
                    "agency_analytics.client_config.load_client", return_value=client_cfg_active
                ):
                    with patch("agency_analytics.client_config.validate_client", return_value=[]):
                        with patch("run_google.resolve_google_credentials", return_value=None):
                            with pytest.raises(SystemExit) as exc:
                                main()
                            assert exc.value.code == 1

        # 5. Success run with oauth_creds
        oauth_creds = {"client_id": "cid", "client_secret": "sec", "refresh_token": "rt"}
        mock_pipeline = MagicMock()
        with patch.object(sys, "argv", ["run_google", "--client", "c"]):
            with patch("os.path.exists", return_value=True):
                with patch(
                    "agency_analytics.client_config.load_client", return_value=client_cfg_active
                ):
                    with patch("agency_analytics.client_config.validate_client", return_value=[]):
                        with patch(
                            "run_google.resolve_google_credentials", return_value=oauth_creds
                        ):
                            with patch("dlt.pipeline", return_value=mock_pipeline):
                                with patch(
                                    "run_google.google_ads_source", return_value="dummy_src"
                                ):
                                    main()
                                    mock_pipeline.run.assert_called_once()
