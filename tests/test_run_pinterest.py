import os
from unittest.mock import MagicMock, patch

import pytest
import yaml

MODULE = "run_pinterest"


def _mock_response(status=200, json_data=None):
    from unittest.mock import MagicMock

    from dlt.sources.helpers import requests

    m = MagicMock(spec=requests.Response)
    m.status_code = status
    m.json.return_value = json_data or {}
    m.headers = {}
    return m


def test_boards_success(monkeypatch):
    boards_response = {
        "items": [
            {
                "id": "board_1",
                "name": "My Board",
                "description": "My pins",
                "pin_count": 50,
                "follower_count": 10,
                "created_at": "2024-01-01T00:00:00Z",
            }
        ],
        "bookmark": None,
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.get",
        lambda url, headers=None, params=None: _mock_response(200, boards_response),
    )

    from run_pinterest import pinterest_source

    source = pinterest_source("mock_token")
    items = list(source.resources["boards"])

    assert len(items) == 1
    assert items[0]["board_id"] == "board_1"
    assert items[0]["name"] == "My Board"
    assert items[0]["description"] == "My pins"
    assert items[0]["pin_count"] == 50
    assert items[0]["follower_count"] == 10
    assert items[0]["created_at"] == "2024-01-01T00:00:00Z"


def test_pins_success(monkeypatch):
    boards_response = {
        "items": [
            {
                "id": "board_1",
                "name": "My Board",
                "pin_count": 1,
                "follower_count": 0,
                "created_at": "",
            }
        ],
        "bookmark": None,
    }
    pins_response = {
        "items": [
            {
                "id": "pin_1",
                "title": "My Pin",
                "description": "A cool pin",
                "link": "https://example.com",
                "destination_url": "https://example.com/dest",
                "pin_count": 5,
                "save_count": 3,
                "created_at": "2024-01-02T00:00:00Z",
            }
        ],
        "bookmark": None,
    }

    call_count = [0]

    def mock_get(url, headers=None, params=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return _mock_response(200, boards_response)
        return _mock_response(200, pins_response)

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_pinterest import pinterest_source

    source = pinterest_source("mock_token")
    items = list(source.resources["pins"])

    assert len(items) == 1
    assert items[0]["pin_id"] == "pin_1"
    assert items[0]["board_id"] == "board_1"
    assert items[0]["title"] == "My Pin"
    assert items[0]["destination_url"] == "https://example.com/dest"
    assert items[0]["pin_count"] == 5
    assert items[0]["save_count"] == 3
    assert items[0]["created_at"] == "2024-01-02T00:00:00Z"


def test_board_insights_success(monkeypatch):
    boards_response = {
        "items": [
            {
                "id": "board_1",
                "name": "My Board",
                "pin_count": 1,
                "follower_count": 0,
                "created_at": "",
            }
        ],
        "bookmark": None,
    }
    insights_response = {
        "date": "2024-01-03",
        "metrics": {
            "IMPRESSION": {"reach": 500, "count": 1000},
            "SAVE": {"count": 50},
            "CLICK": {"count": 25},
        },
    }

    call_count = [0]

    def mock_get(url, headers=None, params=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return _mock_response(200, boards_response)
        return _mock_response(200, insights_response)

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_pinterest import pinterest_source

    source = pinterest_source("mock_token")
    items = list(source.resources["board_insights"])

    assert len(items) == 1
    assert items[0]["board_id"] == "board_1"
    assert items[0]["report_date"] == "2024-01-03"
    assert items[0]["reach"] == 500
    assert items[0]["impressions"] == 1000
    assert items[0]["saves"] == 50
    assert items[0]["clicks"] == 25


def test_rate_limit(monkeypatch):
    call_count = [0]

    def mock_get(url, headers=None, params=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return _mock_response(429, {})
        return _mock_response(
            200,
            {
                "items": [
                    {
                        "id": "board_1",
                        "name": "B",
                        "pin_count": 0,
                        "follower_count": 0,
                        "created_at": "",
                    }
                ],
                "bookmark": None,
            },
        )

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)
    monkeypatch.setattr(f"{MODULE}.time.sleep", lambda s: None)

    from run_pinterest import pinterest_source

    source = pinterest_source("mock_token")
    items = list(source.resources["boards"])

    assert len(items) == 1
    assert call_count[0] == 2


def test_token_expired(monkeypatch):
    monkeypatch.setattr(
        f"{MODULE}.requests.get", lambda url, headers=None, params=None: _mock_response(401, {})
    )

    from run_pinterest import pinterest_source

    source = pinterest_source("mock_token")
    with pytest.raises(Exception, match="Token expired"):
        list(source.resources["boards"])


def test_pins_with_board_id(monkeypatch):
    pins_response = {
        "items": [
            {
                "id": "pin_1",
                "title": "Pin",
                "pin_count": 1,
                "save_count": 0,
            }
        ],
        "bookmark": None,
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.get",
        lambda url, headers=None, params=None: _mock_response(200, pins_response),
    )

    from run_pinterest import pinterest_source

    source = pinterest_source("mock_token", board_id="specific_board")
    items = list(source.resources["pins"])

    assert len(items) == 1
    assert items[0]["pin_id"] == "pin_1"
    assert items[0]["board_id"] == "specific_board"


def test_connector_key_and_field():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    pi_cfg = template["connectors"]["pinterest"]
    assert "board_id" in pi_cfg
    assert "token_env" in pi_cfg


def test_do_request_error_branches():
    from dlt.sources.helpers import requests as dlt_requests
    from run_pinterest import _do_request

    # RequestException
    with patch(
        "run_pinterest.requests.get", side_effect=dlt_requests.RequestException("Conn drop")
    ):
        with patch("time.sleep"):
            with pytest.raises(dlt_requests.RequestException):
                _do_request("http://pi.test", {}, {}, "ctx", retries=1)

    # 429 rate limit exceeded
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {"Retry-After": "10"}
    with patch("run_pinterest.requests.get", return_value=mock_resp_429):
        with patch("time.sleep"):
            with pytest.raises(Exception, match="Rate limit exceeded"):
                _do_request("http://pi.test", {}, {}, "ctx", retries=1)

    # status != 200 with json error
    mock_resp_err = MagicMock()
    mock_resp_err.status_code = 400
    mock_resp_err.json.return_value = {"code": 400, "message": "Bad request"}
    with patch("run_pinterest.requests.get", return_value=mock_resp_err):
        with pytest.raises(Exception, match="API error 400"):
            _do_request("http://pi.test", {}, {}, "ctx", retries=1)

    # status != 200 without json error
    mock_resp_err2 = MagicMock()
    mock_resp_err2.status_code = 503
    mock_resp_err2.reason = "Service Unavailable"
    mock_resp_err2.json.side_effect = ValueError()
    with patch("run_pinterest.requests.get", return_value=mock_resp_err2):
        with pytest.raises(Exception, match="API error 503"):
            _do_request("http://pi.test", {}, {}, "ctx", retries=1)

    # JSON parse error on 200
    mock_resp_bad = MagicMock()
    mock_resp_bad.status_code = 200
    mock_resp_bad.json.side_effect = ValueError("Corrupt body")
    with patch("run_pinterest.requests.get", return_value=mock_resp_bad):
        with pytest.raises(ValueError):
            _do_request("http://pi.test", {}, {}, "ctx", retries=1)


def test_get_board_insights_empty_boards():
    from run_pinterest import get_board_insights

    with patch("run_pinterest._list_board_ids", return_value=[]):
        res = list(get_board_insights("tok"))
        assert res == []


def test_pinterest_main_cli(tmp_path, monkeypatch):
    import runpy
    import sys

    # 1. Client file not found -> exit 1
    monkeypatch.setattr(sys, "argv", ["run_pinterest", "--client", "missing"])
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(tmp_path / "absent.yml")
    ):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path("src/connectors/run_pinterest.py", run_name="__main__")
        assert exc.value.code == 1

    fake_file = tmp_path / "c.yml"
    fake_file.write_text("active: false\n")

    # 2. Inactive client -> exit 0
    monkeypatch.setattr(sys, "argv", ["run_pinterest", "--client", "inactive"])
    with patch("agency_analytics.client_config.client_file_path", return_value=str(fake_file)):
        with patch("agency_analytics.client_config.load_client", return_value={"active": False}):
            with pytest.raises(SystemExit) as exc:
                runpy.run_path("src/connectors/run_pinterest.py", run_name="__main__")
            assert exc.value.code == 0

    # 3. Validation error -> exit 1
    monkeypatch.setattr(sys, "argv", ["run_pinterest", "--client", "invalid"])
    with patch("agency_analytics.client_config.client_file_path", return_value=str(fake_file)):
        with patch("agency_analytics.client_config.load_client", return_value={"active": True}):
            with patch("agency_analytics.client_config.validate_client", return_value=["Err"]):
                with pytest.raises(SystemExit) as exc:
                    runpy.run_path("src/connectors/run_pinterest.py", run_name="__main__")
                assert exc.value.code == 1

    # 4. Connector disabled -> exit 0
    client_cfg = {"active": True, "connectors": {"pinterest": {"enabled": False}}}
    monkeypatch.setattr(sys, "argv", ["run_pinterest", "--client", "disabled"])
    with patch("agency_analytics.client_config.client_file_path", return_value=str(fake_file)):
        with patch("agency_analytics.client_config.load_client", return_value=client_cfg):
            with patch("agency_analytics.client_config.validate_client", return_value=[]):
                with pytest.raises(SystemExit) as exc:
                    runpy.run_path("src/connectors/run_pinterest.py", run_name="__main__")
                assert exc.value.code == 0

    # 5. Missing envs -> exit 1
    client_cfg_active = {"active": True, "connectors": {"pinterest": {"enabled": True}}}
    monkeypatch.setattr(sys, "argv", ["run_pinterest", "--client", "missing_env"])
    with patch("agency_analytics.client_config.client_file_path", return_value=str(fake_file)):
        with patch("agency_analytics.client_config.load_client", return_value=client_cfg_active):
            with patch("agency_analytics.client_config.validate_client", return_value=[]):
                with patch(
                    "agency_analytics.client_config.missing_envs", return_value=["MISSING_KEY"]
                ):
                    with pytest.raises(SystemExit) as exc:
                        runpy.run_path("src/connectors/run_pinterest.py", run_name="__main__")
                    assert exc.value.code == 1

    # 6. Success run
    client_cfg_good = {
        "active": True,
        "connectors": {"pinterest": {"enabled": True, "token_env": "PI_TOKEN", "board_id": "b123"}},
    }
    monkeypatch.setattr(sys, "argv", ["run_pinterest", "--client", "good"])
    monkeypatch.setenv("PI_TOKEN", "valid_pi_tok")
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = "pi_ok"
    with patch("agency_analytics.client_config.client_file_path", return_value=str(fake_file)):
        with patch("agency_analytics.client_config.load_client", return_value=client_cfg_good):
            with patch("agency_analytics.client_config.validate_client", return_value=[]):
                with patch("agency_analytics.client_config.missing_envs", return_value=[]):
                    with patch("dlt.pipeline", return_value=mock_pipeline):
                        with patch("run_pinterest.pinterest_source", return_value="dummy_pi_src"):
                            runpy.run_path("src/connectors/run_pinterest.py", run_name="__main__")
                            mock_pipeline.run.assert_called_once()
