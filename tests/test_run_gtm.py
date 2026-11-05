import json
from unittest.mock import MagicMock, patch

import pytest
from run_gtm import (
    _headers,
    get_containers,
    get_tags,
    get_triggers,
    gtm_source,
)

SAMPLE_CONTAINERS_RESPONSE = {
    "container": [
        {
            "containerId": "1001",
            "accountId": "123456789",
            "name": "Main Container",
            "publicId": "GTM-ABC123",
            "usageContext": ["web", "amp"],
        },
        {
            "containerId": "1002",
            "accountId": "123456789",
            "name": "iOS Container",
            "publicId": "GTM-DEF456",
            "usageContext": ["ios"],
        },
    ]
}

SAMPLE_WORKSPACES_RESPONSE = {
    "workspace": [
        {
            "workspaceId": "w1",
            "name": "Default Workspace",
        }
    ]
}

SAMPLE_TAGS_RESPONSE = {
    "tag": [
        {
            "tagId": "t1",
            "type": "html",
            "name": "Google Analytics Tag",
            "firingTriggerId": ["tr1", "tr2"],
            "blockingTriggerId": ["tr3"],
            "workspaceId": "w1",
        }
    ]
}

SAMPLE_TRIGGERS_RESPONSE = {
    "trigger": [
        {
            "triggerId": "tr1",
            "type": "pageview",
            "name": "All Pages",
            "filter": [
                {
                    "type": "contains",
                    "parameter": [
                        {"type": "template", "key": "arg0", "value": "{{Page URL}}"},
                        {"type": "template", "key": "arg1", "value": "/products"},
                    ],
                }
            ],
        }
    ]
}


class TestHeaders:
    def test_headers_format(self):
        h = _headers("test-token")
        assert h["Authorization"] == "Bearer test-token"


class TestContainers:
    @patch("run_gtm.requests.get")
    def test_containers_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE
        mock_get.return_value = mock_resp

        rows = list(get_containers("accounts/123456789", "token"))
        assert len(rows) == 2

        c1 = rows[0]
        assert c1["container_id"] == "1001"
        assert c1["account_id"] == "123456789"
        assert c1["name"] == "Main Container"
        assert c1["public_id"] == "GTM-ABC123"
        assert json.loads(c1["usage_context"]) == ["web", "amp"]

        c2 = rows[1]
        assert c2["container_id"] == "1002"
        assert c2["public_id"] == "GTM-DEF456"

    @patch("run_gtm.requests.get")
    def test_rate_limit_retry(self, mock_get):
        rate_resp = MagicMock()
        rate_resp.status_code = 429
        rate_resp.headers = {"Retry-After": "1"}

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        mock_get.side_effect = [rate_resp, ok_resp]

        rows = list(get_containers("accounts/123456789", "token"))
        assert len(rows) == 2

    @patch("run_gtm.requests.get")
    def test_token_expired(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"error": {"code": 401, "message": "Token expired"}}
        mock_get.return_value = mock_resp

        with pytest.raises(Exception, match="Token expired"):
            list(get_containers("accounts/123456789", "bad-token"))

    @patch("run_gtm.requests.get")
    def test_token_manager_refresh_on_401(self, mock_get):
        err_resp = MagicMock()
        err_resp.status_code = 200
        err_resp.json.return_value = {"error": {"code": 401, "message": "Token expired"}}

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        mock_get.side_effect = [err_resp, ok_resp]

        mock_manager = MagicMock()
        mock_manager.get_token.side_effect = ["expired_tok", "new_tok"]

        rows = list(get_containers("accounts/123456789", token_manager=mock_manager))
        assert len(rows) == 2
        assert mock_get.call_count == 2
        second_call_headers = mock_get.call_args_list[1][1]["headers"]
        assert second_call_headers["Authorization"] == "Bearer new_tok"


class TestTags:
    @patch("run_gtm.requests.get")
    def test_tags_success(self, mock_get):
        containers_resp = MagicMock()
        containers_resp.status_code = 200
        containers_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        ws_resp = MagicMock()
        ws_resp.status_code = 200
        ws_resp.json.return_value = SAMPLE_WORKSPACES_RESPONSE

        tags_resp = MagicMock()
        tags_resp.status_code = 200
        tags_resp.json.return_value = SAMPLE_TAGS_RESPONSE

        mock_get.side_effect = [containers_resp, ws_resp, tags_resp, ws_resp, tags_resp]

        rows = list(get_tags("accounts/123456789", "token"))
        assert len(rows) == 2

        tag = rows[0]
        assert tag["tag_id"] == "t1"
        assert tag["container_id"] == "1001"
        assert tag["type"] == "html"
        assert tag["name"] == "Google Analytics Tag"
        assert json.loads(tag["firing_triggers"]) == ["tr1", "tr2"]
        assert json.loads(tag["blocking_triggers"]) == ["tr3"]
        assert "tagmanager.google.com" in tag["tag_manager_url"]

    @patch("run_gtm.requests.get")
    def test_multiple_containers(self, mock_get):
        containers_resp = MagicMock()
        containers_resp.status_code = 200
        containers_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        ws_resp = MagicMock()
        ws_resp.status_code = 200
        ws_resp.json.return_value = SAMPLE_WORKSPACES_RESPONSE

        tags_resp_1 = MagicMock()
        tags_resp_1.status_code = 200
        tags_resp_1.json.return_value = {
            "tag": [
                {
                    "tagId": "t1",
                    "type": "html",
                    "name": "Tag in Container 1",
                    "firingTriggerId": [],
                    "blockingTriggerId": [],
                    "workspaceId": "w1",
                }
            ]
        }

        tags_resp_2 = MagicMock()
        tags_resp_2.status_code = 200
        tags_resp_2.json.return_value = {
            "tag": [
                {
                    "tagId": "t2",
                    "type": "gtag",
                    "name": "Tag in Container 2",
                    "firingTriggerId": [],
                    "blockingTriggerId": [],
                    "workspaceId": "w2",
                }
            ]
        }

        mock_get.side_effect = [containers_resp, ws_resp, tags_resp_1, ws_resp, tags_resp_2]

        rows = list(get_tags("accounts/123456789", "token"))
        assert len(rows) == 2
        assert rows[0]["container_id"] == "1001"
        assert rows[1]["container_id"] == "1002"

    @patch("run_gtm.requests.get")
    def test_rate_limit_on_containers(self, mock_get):
        rate_resp = MagicMock()
        rate_resp.status_code = 429
        rate_resp.headers = {"Retry-After": "1"}

        containers_resp = MagicMock()
        containers_resp.status_code = 200
        containers_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        ws_resp = MagicMock()
        ws_resp.status_code = 200
        ws_resp.json.return_value = SAMPLE_WORKSPACES_RESPONSE

        tags_resp = MagicMock()
        tags_resp.status_code = 200
        tags_resp.json.return_value = SAMPLE_TAGS_RESPONSE

        mock_get.side_effect = [rate_resp, containers_resp, ws_resp, tags_resp, ws_resp, tags_resp]

        rows = list(get_tags("accounts/123456789", "token"))
        assert len(rows) == 2


class TestTriggers:
    @patch("run_gtm.requests.get")
    def test_triggers_success(self, mock_get):
        containers_resp = MagicMock()
        containers_resp.status_code = 200
        containers_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        ws_resp = MagicMock()
        ws_resp.status_code = 200
        ws_resp.json.return_value = SAMPLE_WORKSPACES_RESPONSE

        triggers_resp = MagicMock()
        triggers_resp.status_code = 200
        triggers_resp.json.return_value = SAMPLE_TRIGGERS_RESPONSE

        mock_get.side_effect = [containers_resp, ws_resp, triggers_resp, ws_resp, triggers_resp]

        rows = list(get_triggers("accounts/123456789", "token"))
        assert len(rows) == 2

        tr = rows[0]
        assert tr["trigger_id"] == "tr1"
        assert tr["container_id"] == "1001"
        assert tr["type"] == "pageview"
        assert tr["name"] == "All Pages"

        filters = json.loads(tr["filter_json"])
        assert len(filters) == 1
        assert filters[0]["type"] == "contains"

    @patch("run_gtm.requests.get")
    def test_empty_triggers(self, mock_get):
        containers_resp = MagicMock()
        containers_resp.status_code = 200
        containers_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        ws_resp = MagicMock()
        ws_resp.status_code = 200
        ws_resp.json.return_value = SAMPLE_WORKSPACES_RESPONSE

        triggers_resp = MagicMock()
        triggers_resp.status_code = 200
        triggers_resp.json.return_value = {"trigger": []}

        mock_get.side_effect = [containers_resp, ws_resp, triggers_resp, ws_resp, triggers_resp]

        rows = list(get_triggers("accounts/123456789", "token"))
        assert len(rows) == 0

    @patch("run_gtm.requests.get")
    def test_token_expired_on_triggers(self, mock_get):
        containers_resp = MagicMock()
        containers_resp.status_code = 200
        containers_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        error_resp = MagicMock()
        error_resp.status_code = 200
        error_resp.json.return_value = {"error": {"code": 401, "message": "Token expired"}}

        mock_get.side_effect = [containers_resp, error_resp]

        with pytest.raises(Exception, match="Token expired"):
            list(get_triggers("accounts/123456789", "bad-token"))


class TestGtmSource:
    @patch("run_gtm.requests.get")
    def test_source_returns_all_resources(self, mock_get):
        def mock_responses(*args, **kwargs):
            url = args[0] if args else ""
            resp = MagicMock()
            resp.status_code = 200

            if "/tags" in url:
                resp.json.return_value = SAMPLE_TAGS_RESPONSE
            elif "/triggers" in url:
                resp.json.return_value = SAMPLE_TRIGGERS_RESPONSE
            else:
                resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE
            return resp

        mock_get.side_effect = mock_responses

        source = gtm_source("accounts/123456789", "token")
        resources = list(source.resources.values())
        assert len(resources) == 3
        names = [r.name for r in resources]
        assert "containers" in names
        assert "tags" in names
        assert "triggers" in names


def test_gtm_handle_response_error_branches():
    from dlt.sources.helpers import requests as dlt_requests
    from run_gtm import _get_with_refresh, _handle_response

    # 429 in data
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"error": {"code": 429}}
    with patch("time.sleep"):
        assert _handle_response(mock_resp, "ctx") is None

    # 404
    mock_resp.json.return_value = {"error": {"code": 404, "message": "Not Found"}}
    with pytest.raises(Exception, match="Resource not found"):
        _handle_response(mock_resp, "ctx")

    # 400
    mock_resp.json.return_value = {"error": {"code": 400, "message": "Bad req"}}
    with pytest.raises(Exception, match="Bad request"):
        _handle_response(mock_resp, "ctx")

    # Generic error
    mock_resp.json.return_value = {"error": {"code": 500, "message": "Server error"}}
    with pytest.raises(Exception, match="API error 500"):
        _handle_response(mock_resp, "ctx")

    # _get_with_refresh RequestException
    with patch("run_gtm.requests.get", side_effect=dlt_requests.RequestException("Conn drop")):
        with pytest.raises(dlt_requests.RequestException):
            _get_with_refresh("http://test", "ctx", access_token="tok")


def test_gtm_main_cli(tmp_path, monkeypatch):
    import runpy
    import sys

    # 1. Client file not found -> exit 1
    monkeypatch.setattr(sys, "argv", ["run_gtm", "--client", "missing"])
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(tmp_path / "absent.yml")
    ):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path("src/connectors/run_gtm.py", run_name="__main__")
        assert exc.value.code == 1

    fake_client_file = tmp_path / "client.yml"
    fake_client_file.write_text("active: false\n")

    # 2. Inactive client -> exit 0
    monkeypatch.setattr(sys, "argv", ["run_gtm", "--client", "inactive"])
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(fake_client_file)
    ):
        with patch("agency_analytics.client_config.load_client", return_value={"active": False}):
            with pytest.raises(SystemExit) as exc:
                runpy.run_path("src/connectors/run_gtm.py", run_name="__main__")
            assert exc.value.code == 0

    # 3. Validation error -> exit 1
    monkeypatch.setattr(sys, "argv", ["run_gtm", "--client", "invalid"])
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(fake_client_file)
    ):
        with patch("agency_analytics.client_config.load_client", return_value={"active": True}):
            with patch("agency_analytics.client_config.validate_client", return_value=["Err"]):
                with pytest.raises(SystemExit) as exc:
                    runpy.run_path("src/connectors/run_gtm.py", run_name="__main__")
                assert exc.value.code == 1

    # 4. Connector disabled -> exit 0
    client_cfg = {"active": True, "connectors": {"gtm": {"enabled": False}}}
    monkeypatch.setattr(sys, "argv", ["run_gtm", "--client", "disabled"])
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(fake_client_file)
    ):
        with patch("agency_analytics.client_config.load_client", return_value=client_cfg):
            with patch("agency_analytics.client_config.validate_client", return_value=[]):
                with pytest.raises(SystemExit) as exc:
                    runpy.run_path("src/connectors/run_gtm.py", run_name="__main__")
                assert exc.value.code == 0

    # 5. Missing credentials -> exit 1
    client_cfg_active = {
        "active": True,
        "connectors": {"gtm": {"enabled": True, "account_path": "12345"}},
    }
    monkeypatch.setattr(sys, "argv", ["run_gtm", "--client", "missing_creds"])
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(fake_client_file)
    ):
        with patch("agency_analytics.client_config.load_client", return_value=client_cfg_active):
            with patch("agency_analytics.client_config.validate_client", return_value=[]):
                with patch(
                    "agency_analytics.google_auth.resolve_google_credentials", return_value=None
                ):
                    with pytest.raises(SystemExit) as exc:
                        runpy.run_path("src/connectors/run_gtm.py", run_name="__main__")
                    assert exc.value.code == 1

    # 6. Success run with oauth_creds
    oauth_creds = {"client_id": "cid", "client_secret": "sec", "refresh_token": "rt"}
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = "gtm_ok"
    monkeypatch.setattr(sys, "argv", ["run_gtm", "--client", "good"])
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(fake_client_file)
    ):
        with patch("agency_analytics.client_config.load_client", return_value=client_cfg_active):
            with patch("agency_analytics.client_config.validate_client", return_value=[]):
                with patch(
                    "agency_analytics.google_auth.resolve_google_credentials",
                    return_value=oauth_creds,
                ):
                    with patch("dlt.pipeline", return_value=mock_pipeline):
                        runpy.run_path("src/connectors/run_gtm.py", run_name="__main__")
                        mock_pipeline.run.assert_called_once()
