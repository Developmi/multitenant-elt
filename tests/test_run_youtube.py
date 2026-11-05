import os
import runpy
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import yaml
from run_youtube import (
    _analytics_query,
    _api_get,
    _handle_response,
    _parse_iso_duration,
    _TokenManager,
    get_channel_stats,
    get_video_daily_analytics,
    get_videos,
    youtube_source,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUN_YOUTUBE_PATH = os.path.join(_REPO_ROOT, "src", "connectors", "run_youtube.py")


class TestParseIsoDuration:
    def test_full_duration(self):
        assert _parse_iso_duration("PT1H30M15S") == 5415

    def test_minutes_only(self):
        assert _parse_iso_duration("PT5M") == 300

    def test_seconds_only(self):
        assert _parse_iso_duration("PT45S") == 45

    def test_hours_only(self):
        assert _parse_iso_duration("PT2H") == 7200

    def test_empty_fallback(self):
        assert _parse_iso_duration("") == 0

    def test_no_match(self):
        assert _parse_iso_duration("P1DT2H") == 0


class TestTokenManager:
    @patch("run_youtube.requests.post")
    @patch("run_youtube.time.time")
    def test_refresh_and_cache(self, mock_time, mock_post):
        mock_time.return_value = 1000.0
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "tok1", "expires_in": 3600}
        mock_post.return_value = mock_resp

        manager = _TokenManager("client-id", "client-secret", "refresh-token")
        assert manager.get_token() == "tok1"
        assert manager.get_token() == "tok1"
        mock_post.assert_called_once()

        mock_time.return_value = 1000.0 + 3600
        mock_resp.json.return_value = {"access_token": "tok2", "expires_in": 3600}
        assert manager.get_token() == "tok2"
        assert mock_post.call_count == 2

    def test_refresh_failure_raises(self):
        with patch("run_youtube.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"error": "invalid_grant"}
            mock_post.return_value = mock_resp

            manager = _TokenManager("client-id", "client-secret", "refresh-token")
            with pytest.raises(Exception, match="invalid_grant"):
                manager.get_token()

    @patch("run_youtube.requests.get")
    def test_401_refresh_and_retry_once(self, mock_get):
        manager = _TokenManager("client-id", "client-secret", "refresh-token")
        with patch.object(manager, "get_token", side_effect=["expired", "fresh"]):
            err_resp = MagicMock()
            err_resp.json.return_value = {"error": {"code": 401, "message": "Invalid Credentials"}}
            ok_resp = MagicMock()
            ok_resp.json.return_value = {
                "columnHeaders": [{"name": "video"}, {"name": "views"}],
                "rows": [["v1", "31"]],
            }
            mock_get.side_effect = [err_resp, ok_resp]

            rows = _analytics_query(
                manager, "UC_test", "2026-01-01", "2026-01-02", "video", "views", "test"
            )
            assert rows == [{"video_id": "v1", "views": "31"}]

    @patch("run_youtube.requests.get")
    def test_401_without_token_manager_raises(self, mock_get):
        err_resp = MagicMock()
        err_resp.json.return_value = {"error": {"code": 401, "message": "Invalid Credentials"}}
        mock_get.return_value = err_resp

        with pytest.raises(Exception, match="Invalid Credentials"):
            _api_get("https://example.com", {"part": "snippet"}, "test", api_key="key")


class TestChannelStats:
    @patch("run_youtube.requests.get")
    @patch("run_youtube.date")
    def test_channel_stats_success(self, mock_date, mock_get):
        mock_date.today.return_value.isoformat.return_value = "2026-07-22"
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "items": [
                {
                    "id": "UC_test",
                    "snippet": {
                        "title": "Test Channel",
                        "description": "A test channel",
                        "publishedAt": "2020-01-01T00:00:00Z",
                    },
                    "statistics": {
                        "subscriberCount": "1500",
                        "viewCount": "75000",
                        "videoCount": "200",
                        "hiddenSubscriberCount": False,
                    },
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU_test_uploads"}},
                    "status": {"privacyStatus": "public"},
                }
            ]
        }
        mock_get.return_value = mock_resp

        rows = list(get_channel_stats("UC_test", "fake-key"))
        assert len(rows) == 1
        row = rows[0]
        assert row["report_date"] == "2026-07-22"
        assert row["subscriber_count"] == 1500
        assert row["view_count"] == 75000
        assert row["video_count"] == 200
        assert row["hidden_subscriber_count"] is False
        assert row["uploads_playlist_id"] == "UU_test_uploads"
        assert row["privacy_status"] == "public"
        assert row["title"] == "Test Channel"

    @patch("run_youtube.requests.get")
    def test_rate_limit_retry(self, mock_get):
        rate_resp = MagicMock()
        rate_resp.json.return_value = {"error": {"code": 429, "message": "Rate Limit Exceeded"}}
        rate_resp.headers = {"Retry-After": "1"}

        ok_resp = MagicMock()
        ok_resp.json.return_value = {
            "items": [
                {
                    "id": "UC_test",
                    "snippet": {},
                    "statistics": {
                        "subscriberCount": "100",
                        "viewCount": "500",
                        "videoCount": "10",
                        "hiddenSubscriberCount": False,
                    },
                }
            ]
        }

        mock_get.side_effect = [rate_resp, ok_resp]
        rows = list(get_channel_stats("UC_test", "key"))
        assert len(rows) == 1

    @patch("run_youtube.time.sleep")
    @patch("run_youtube.requests.get")
    def test_rate_limit_quota_exceeded(self, mock_get, mock_sleep):
        rate_resp = MagicMock()
        rate_resp.json.return_value = {
            "error": {"code": 403, "message": "quotaExceeded – rate limiting"}
        }

        ok_resp = MagicMock()
        ok_resp.json.return_value = {
            "items": [
                {
                    "id": "UC_test",
                    "snippet": {},
                    "statistics": {
                        "subscriberCount": "100",
                        "viewCount": "500",
                        "videoCount": "10",
                        "hiddenSubscriberCount": False,
                    },
                }
            ]
        }

        mock_get.side_effect = [rate_resp, ok_resp]
        rows = list(get_channel_stats("UC_test", "key"))
        assert len(rows) == 1

    @patch("run_youtube.requests.get")
    def test_invalid_key(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"code": 400, "message": "Bad Request"}}
        mock_get.return_value = mock_resp

        with pytest.raises(Exception, match="Bad request"):
            list(get_channel_stats("UC_test", "bad-key"))

    @patch("run_youtube.requests.get")
    def test_no_channel_found(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_get.return_value = mock_resp

        rows = list(get_channel_stats("UC_nonexistent", "key"))
        assert len(rows) == 0


class TestVideos:
    @patch("run_youtube.requests.get")
    def test_videos_success(self, mock_get):
        playlist_resp = MagicMock()
        playlist_resp.json.return_value = {
            "items": [
                {"contentDetails": {"videoId": "v1"}},
                {"contentDetails": {"videoId": "v2"}},
            ]
        }

        stats_resp = MagicMock()
        stats_resp.json.return_value = {
            "items": [
                {
                    "id": "v1",
                    "snippet": {
                        "title": "Video One",
                        "publishedAt": "2024-06-01T10:00:00Z",
                        "categoryId": "22",
                        "description": "desc one",
                    },
                    "statistics": {
                        "viewCount": "5000",
                        "likeCount": "300",
                        "commentCount": "50",
                    },
                    "contentDetails": {"duration": "PT10M30S"},
                    "status": {"privacyStatus": "public", "license": "youtube"},
                    "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Foo"]},
                },
                {
                    "id": "v2",
                    "snippet": {
                        "title": "Video Two",
                        "publishedAt": "2024-06-02T12:00:00Z",
                        "categoryId": "23",
                    },
                    "statistics": {
                        "viewCount": "3000",
                        "likeCount": "150",
                        "commentCount": "20",
                    },
                    "contentDetails": {"duration": "PT5M15S"},
                    "status": {},
                    "topicDetails": {},
                },
            ]
        }

        mock_get.side_effect = [playlist_resp, stats_resp]

        rows = list(get_videos("UC_test", "UU_test", "key"))
        assert len(rows) == 2

        assert rows[0]["video_id"] == "v1"
        assert rows[0]["title"] == "Video One"
        assert rows[0]["view_count"] == 5000
        assert rows[0]["like_count"] == 300
        assert rows[0]["comment_count"] == 50
        assert rows[0]["dislike_count"] == 0
        assert rows[0]["duration"] == "PT10M30S"
        assert rows[0]["category_id"] == "22"
        assert rows[0]["description"] == "desc one"
        assert rows[0]["privacy_status"] == "public"
        assert rows[0]["license"] == "youtube"
        assert rows[0]["topic_categories"] == ["https://en.wikipedia.org/wiki/Foo"]

        assert rows[1]["video_id"] == "v2"
        assert rows[1]["duration"] == "PT5M15S"
        assert rows[1]["category_id"] == "23"
        assert rows[1]["privacy_status"] is None

    @patch("run_youtube.requests.get")
    def test_pagination(self, mock_get):
        page1_resp = MagicMock()
        page1_resp.json.return_value = {
            "items": [{"contentDetails": {"videoId": "v1"}}, {"contentDetails": {"videoId": "v2"}}],
            "nextPageToken": "token_page_2",
        }

        page2_resp = MagicMock()
        page2_resp.json.return_value = {
            "items": [{"contentDetails": {"videoId": "v3"}}],
        }

        stats_resp = MagicMock()
        stats_resp.json.return_value = {
            "items": [
                {
                    "id": f"v{i}",
                    "snippet": {"title": f"V{i}", "publishedAt": "2024-01-01"},
                    "statistics": {
                        "viewCount": str(i * 100),
                        "likeCount": str(i * 10),
                        "commentCount": str(i * 5),
                    },
                    "contentDetails": {"duration": "PT5M"},
                }
                for i in range(1, 4)
            ]
        }

        mock_get.side_effect = [page1_resp, page2_resp, stats_resp]

        rows = list(get_videos("UC_test", "UU_test", "key"))
        assert len(rows) == 3
        assert [r["video_id"] for r in rows] == ["v1", "v2", "v3"]

    @patch("run_youtube.requests.get")
    def test_rate_limit_during_enumeration(self, mock_get):
        rate_resp = MagicMock()
        rate_resp.json.return_value = {"error": {"code": 429, "message": "Rate limit"}}
        rate_resp.headers = {"Retry-After": "1"}

        ok_resp = MagicMock()
        ok_resp.json.return_value = {"items": []}

        mock_get.side_effect = [rate_resp, ok_resp]

        rows = list(get_videos("UC_test", "UU_test", "key"))
        assert len(rows) == 0

    @patch("run_youtube.requests.get")
    def test_rate_limit_during_stats(self, mock_get):
        playlist_resp = MagicMock()
        playlist_resp.json.return_value = {"items": [{"contentDetails": {"videoId": "v1"}}]}

        rate_resp = MagicMock()
        rate_resp.json.return_value = {"error": {"code": 429, "message": "Rate limit"}}
        rate_resp.headers = {"Retry-After": "1"}

        stats_resp = MagicMock()
        stats_resp.json.return_value = {
            "items": [
                {
                    "id": "v1",
                    "snippet": {"title": "V1", "publishedAt": "2024-01-01"},
                    "statistics": {
                        "viewCount": "100",
                        "likeCount": "10",
                        "commentCount": "5",
                    },
                    "contentDetails": {"duration": "PT5M"},
                }
            ]
        }

        mock_get.side_effect = [playlist_resp, rate_resp, stats_resp]

        rows = list(get_videos("UC_test", "UU_test", "key"))
        assert len(rows) == 1

    @patch("run_youtube.requests.get")
    def test_no_videos_found(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_get.return_value = mock_resp

        rows = list(get_videos("UC_empty", "UU_empty", "key"))
        assert len(rows) == 0


class TestVideoDailyAnalytics:
    @patch("run_youtube.date")
    def test_video_daily_analytics_success(self, mock_date):
        mock_date.today.return_value = date(2026, 7, 22)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        manager = _TokenManager("client-id", "client-secret", "refresh-token")

        with (
            patch.object(manager, "get_token", return_value="tok"),
            patch("run_youtube.requests.get") as mock_get,
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "columnHeaders": [
                    {"name": "day", "columnType": "DIMENSION"},
                    {"name": "views", "columnType": "METRIC"},
                    {"name": "estimatedMinutesWatched", "columnType": "METRIC"},
                    {"name": "averageViewDuration", "columnType": "METRIC"},
                ],
                "rows": [["2026-07-01", "31", "40", "77.4"]],
            }
            mock_get.return_value = mock_resp

            rows = list(get_video_daily_analytics("UC_test", manager, "2026-07-01"))
            assert len(rows) == 1
            row = rows[0]
            assert row["report_date"] == "2026-07-01"
            assert row["views"] == "31"
            assert row["estimated_minutes_watched"] == "40"
            assert row["average_view_duration"] == "77.4"

    @patch("run_youtube.date")
    def test_empty_window_skipped(self, mock_date):
        mock_date.today.return_value = date(2026, 7, 22)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        manager = _TokenManager("client-id", "client-secret", "refresh-token")

        with (
            patch.object(manager, "get_token", return_value="tok"),
            patch("run_youtube.requests.get") as mock_get,
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "error": {"code": 400, "message": "The query did not return any data."}
            }
            mock_get.return_value = mock_resp

            rows = list(get_video_daily_analytics("UC_test", manager, "2026-07-01"))
            assert rows == []


class TestYoutubeSource:
    @patch("run_youtube.requests.get")
    def test_source_returns_public_resources(self, mock_get):
        channel_resp = MagicMock()
        channel_resp.json.return_value = {
            "items": [
                {
                    "id": "UC_test",
                    "snippet": {},
                    "statistics": {
                        "subscriberCount": "100",
                        "viewCount": "500",
                        "videoCount": "10",
                        "hiddenSubscriberCount": False,
                    },
                }
            ]
        }
        mock_get.return_value = channel_resp

        source = youtube_source("UC_test", "key")
        resources = list(source.resources.values())
        names = [r.name for r in resources]
        assert len(resources) == 8
        assert set(names) == {
            "channel_stats",
            "uploaded_videos",
            "videos",
            "playlists",
            "playlist_items",
            "comment_threads",
            "channel_sections",
            "video_categories",
        }

    @patch("run_youtube.requests.get")
    def test_source_with_oauth_includes_analytics(self, mock_get):
        channel_resp = MagicMock()
        channel_resp.json.return_value = {"items": []}
        mock_get.return_value = channel_resp

        oauth_config = {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
        }
        source = youtube_source("UC_test", "key", oauth_config=oauth_config)
        names = [r.name for r in source.resources.values()]
        assert len(names) == 13
        assert "video_daily_analytics" in names
        assert "video_analytics" in names
        assert "traffic_source_analytics" in names
        assert "device_analytics" in names
        assert "country_analytics" in names
        assert "captions" not in names

    @patch("run_youtube.requests.get")
    def test_source_with_captions_enabled(self, mock_get):
        channel_resp = MagicMock()
        channel_resp.json.return_value = {"items": []}
        mock_get.return_value = channel_resp

        oauth_config = {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
        }
        source = youtube_source("UC_test", "key", oauth_config=oauth_config, captions_enabled=True)
        names = [r.name for r in source.resources.values()]
        assert "captions" in names

    # ── SDD-F WU4: dlt nullable column hints (YLD-R2/R3, design D5) ──────────
    # dlt only materializes columns that receive data; optional/state-gated
    # youtube fields (default_language, live-stream timestamps, etc.) would be
    # dropped from the raw tables without explicit ``columns=`` hints (obs #625
    # bug class, IG WU5 precedent run_instagram.py:466-479). Each test reads
    # the declared hints statically from the built source (no iteration) and
    # asserts name/data_type/nullable per resource.

    @staticmethod
    def _hints(resource) -> dict:
        return {name: col for name, col in (resource.columns or {}).items()}

    def _assert_hint(self, hints: dict, column: str, data_type: str) -> None:
        assert column in hints, f"column {column} needs a dlt nullable hint"
        assert hints[column]["data_type"] == data_type
        assert hints[column]["nullable"] is True

    @patch("run_youtube.requests.get")
    def test_channel_stats_declares_default_language_hint(self, mock_get):
        channel_resp = MagicMock()
        channel_resp.json.return_value = {"items": []}
        mock_get.return_value = channel_resp

        source = youtube_source("UC_test", "key")
        hints = self._hints(source.resources["channel_stats"])
        self._assert_hint(hints, "default_language", "text")

    @patch("run_youtube.requests.get")
    def test_uploaded_videos_declares_video_published_at_hint(self, mock_get):
        channel_resp = MagicMock()
        channel_resp.json.return_value = {"items": []}
        mock_get.return_value = channel_resp

        source = youtube_source("UC_test", "key")
        hints = self._hints(source.resources["uploaded_videos"])
        self._assert_hint(hints, "video_published_at", "timestamp")

    @patch("run_youtube.requests.get")
    def test_videos_declares_kids_and_live_stream_hints(self, mock_get):
        channel_resp = MagicMock()
        channel_resp.json.return_value = {"items": []}
        mock_get.return_value = channel_resp

        source = youtube_source("UC_test", "key")
        hints = self._hints(source.resources["videos"])
        self._assert_hint(hints, "self_declared_made_for_kids", "bool")
        self._assert_hint(hints, "live_broadcast_content", "text")
        self._assert_hint(hints, "live_concurrent_viewers", "bigint")
        self._assert_hint(hints, "live_scheduled_start_time", "timestamp")
        self._assert_hint(hints, "live_actual_start_time", "timestamp")
        self._assert_hint(hints, "live_actual_end_time", "timestamp")

    @patch("run_youtube.requests.get")
    def test_playlists_declares_updated_at_hint(self, mock_get):
        channel_resp = MagicMock()
        channel_resp.json.return_value = {"items": []}
        mock_get.return_value = channel_resp

        source = youtube_source("UC_test", "key")
        hints = self._hints(source.resources["playlists"])
        self._assert_hint(hints, "updated_at", "timestamp")

    @patch("run_youtube.requests.get")
    def test_playlist_items_declares_video_published_at_hint(self, mock_get):
        channel_resp = MagicMock()
        channel_resp.json.return_value = {"items": []}
        mock_get.return_value = channel_resp

        source = youtube_source("UC_test", "key")
        hints = self._hints(source.resources["playlist_items"])
        self._assert_hint(hints, "video_published_at", "timestamp")


class TestHandleResponse:
    def test_handle_response_valid(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": [{"id": "test"}]}
        result = _handle_response(mock_resp, "test")
        assert result == {"items": [{"id": "test"}]}

    def test_handle_response_400(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"code": 400, "message": "Bad Request"}}
        with pytest.raises(Exception, match="Bad request"):
            _handle_response(mock_resp, "test")

    def test_handle_response_401_raises_auth_error(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"code": 401, "message": "Invalid Credentials"}}
        with pytest.raises(Exception, match="Invalid Credentials"):
            _handle_response(mock_resp, "test")

    def test_handle_response_429_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"code": 429, "message": "Rate limit"}}
        mock_resp.headers = {"Retry-After": "5"}
        result = _handle_response(mock_resp, "test")
        assert result is None

    def test_handle_response_500_raises(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"code": 500, "message": "internal error"}}
        with pytest.raises(Exception, match="API error 500"):
            _handle_response(mock_resp, "test")

    def test_api_get_request_exception_raises(self):
        from dlt.sources.helpers import requests as dlt_requests
        from run_youtube import _api_get

        with patch(
            "dlt.sources.helpers.requests.get",
            side_effect=dlt_requests.RequestException("Conn drop"),
        ):
            with pytest.raises(dlt_requests.RequestException):
                _api_get("http://yt.test", {}, "test_ctx", api_key="k")


class TestMainBootstrap:
    """Main-driven regression (SDD-C WU2, M-S2b): youtube leniency preserved.
    Approval-style test — passes before and after the WU2 hunks; its failure
    mode is the env guard being wrongly added to run_youtube (missing_envs
    would turn the graceful API-key skip into exit 1).
    """

    def test_api_key_unset_no_trio_exits_0_lenient(self, tmp_path, monkeypatch, capsys):
        client_dir = tmp_path / "clients"
        client_dir.mkdir()
        (client_dir / "yt_client.yml").write_text(
            yaml.safe_dump(
                {
                    "client_id": "yt_client",
                    "active": True,
                    "connectors": {
                        "youtube": {
                            "enabled": True,
                            "channel_id": "UC_test_channel",
                            "token_env": "YOUTUBE_API_KEY_UNSET_TEST",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("CLIENTS_DIR", str(client_dir))
        monkeypatch.delenv("YOUTUBE_API_KEY_UNSET_TEST", raising=False)

        with patch("sys.argv", ["run_youtube.py", "--client", "yt_client"]):
            with pytest.raises(SystemExit) as exc:
                runpy.run_path(_RUN_YOUTUBE_PATH, run_name="__main__")
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "[YOUTUBE]" in out
        assert "Environment variable YOUTUBE_API_KEY_UNSET_TEST is not set." in out
        assert "Skipping YouTube" in out

    def test_client_file_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLIENTS_DIR", str(tmp_path))
        with patch("sys.argv", ["run_youtube.py", "--client", "missing"]):
            with pytest.raises(SystemExit) as exc:
                runpy.run_path(_RUN_YOUTUBE_PATH, run_name="__main__")
        assert exc.value.code == 1

    def test_inactive_client(self, tmp_path, monkeypatch):
        client_dir = tmp_path / "clients"
        client_dir.mkdir(exist_ok=True)
        (client_dir / "inactive.yml").write_text("active: false\n", encoding="utf-8")
        monkeypatch.setenv("CLIENTS_DIR", str(client_dir))

        with patch("sys.argv", ["run_youtube.py", "--client", "inactive"]):
            with pytest.raises(SystemExit) as exc:
                runpy.run_path(_RUN_YOUTUBE_PATH, run_name="__main__")
        assert exc.value.code == 0

    def test_connector_disabled(self, tmp_path, monkeypatch):
        client_dir = tmp_path / "clients"
        client_dir.mkdir(exist_ok=True)
        (client_dir / "disabled.yml").write_text(
            "active: true\nconnectors:\n  youtube:\n    enabled: false\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLIENTS_DIR", str(client_dir))

        with patch("sys.argv", ["run_youtube.py", "--client", "disabled"]):
            with pytest.raises(SystemExit) as exc:
                runpy.run_path(_RUN_YOUTUBE_PATH, run_name="__main__")
        assert exc.value.code == 0

    def test_success_run_with_oauth(self, tmp_path, monkeypatch):
        client_dir = tmp_path / "clients"
        client_dir.mkdir(exist_ok=True)
        (client_dir / "good.yml").write_text(
            yaml.safe_dump(
                {
                    "client_id": "good",
                    "active": True,
                    "connectors": {
                        "youtube": {
                            "enabled": True,
                            "channel_id": "UC123",
                            "token_env": "YT_API_KEY",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("CLIENTS_DIR", str(client_dir))
        monkeypatch.setenv("YT_API_KEY", "valid_key")

        mock_pipeline = MagicMock()
        mock_pipeline.run.return_value = "yt_ok"
        oauth_creds = {"client_id": "cid", "client_secret": "sec", "refresh_token": "rt"}

        mock_ch_resp = MagicMock()
        mock_ch_resp.status_code = 200
        mock_ch_resp.json.return_value = {
            "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UPL123"}}}]
        }

        with patch("sys.argv", ["run_youtube.py", "--client", "good"]):
            with patch(
                "agency_analytics.google_auth.resolve_google_credentials", return_value=oauth_creds
            ):
                with patch("dlt.sources.helpers.requests.get", return_value=mock_ch_resp):
                    with patch("dlt.pipeline", return_value=mock_pipeline):
                        runpy.run_path(_RUN_YOUTUBE_PATH, run_name="__main__")
                        mock_pipeline.run.assert_called_once()

    def test_success_run_with_legacy_oauth_env_keys(self, tmp_path, monkeypatch):
        client_dir = tmp_path / "clients"
        client_dir.mkdir(exist_ok=True)
        (client_dir / "legacy_yt.yml").write_text(
            yaml.safe_dump(
                {
                    "client_id": "legacy_yt",
                    "active": True,
                    "connectors": {
                        "youtube": {
                            "enabled": True,
                            "channel_id": "UC123",
                            "token_env": "YT_API_KEY",
                            "oauth_client_id_env": "LEGACY_CID",
                            "oauth_client_secret_env": "LEGACY_SEC",
                            "oauth_refresh_token_env": "LEGACY_RT",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("CLIENTS_DIR", str(client_dir))
        monkeypatch.setenv("YT_API_KEY", "valid_key")
        monkeypatch.setenv("LEGACY_CID", "legacy_client_id")
        monkeypatch.setenv("LEGACY_SEC", "legacy_client_secret")
        monkeypatch.setenv("LEGACY_RT", "legacy_refresh_token")

        mock_pipeline = MagicMock()
        mock_pipeline.run.return_value = "yt_ok"

        mock_ch_resp = MagicMock()
        mock_ch_resp.status_code = 200
        mock_ch_resp.json.return_value = {
            "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UPL123"}}}]
        }

        with patch("sys.argv", ["run_youtube.py", "--client", "legacy_yt"]):
            with patch(
                "agency_analytics.google_auth.resolve_google_credentials", return_value=None
            ):
                with patch("dlt.sources.helpers.requests.get", return_value=mock_ch_resp):
                    with patch("dlt.pipeline", return_value=mock_pipeline):
                        runpy.run_path(_RUN_YOUTUBE_PATH, run_name="__main__")
                        mock_pipeline.run.assert_called_once()


class TestAdditionalResources:
    @patch("run_youtube._api_get")
    def test_get_playlist_items(self, mock_api_get):
        from run_youtube import get_playlist_items

        mock_api_get.side_effect = [
            {"items": [{"id": "pl1"}]},  # get_playlists
            {
                "items": [
                    {
                        "id": "item1",
                        "snippet": {
                            "playlistId": "pl1",
                            "title": "Item Title",
                            "position": 0,
                            "publishedAt": "2024-01-01T00:00:00Z",
                            "videoPublishedAt": "2024-01-01T00:00:00Z",
                        },
                        "contentDetails": {"videoId": "vid1"},
                        "status": {"privacyStatus": "public"},
                    }
                ],
                "nextPageToken": None,
            },
        ]

        items = list(get_playlist_items("UC123", "uploads_pl", "key"))
        assert len(items) == 1
        assert items[0]["playlist_item_id"] == "item1"
        assert items[0]["video_id"] == "vid1"

    @patch("run_youtube._api_get")
    def test_get_playlist_items_fallback_to_uploads(self, mock_api_get):
        from run_youtube import get_playlist_items

        mock_api_get.side_effect = [
            {"items": []},  # no playlists returned
            {
                "items": [
                    {
                        "id": "upl_item",
                        "snippet": {"playlistId": "upl_pl", "title": "Upl"},
                        "contentDetails": {"videoId": "v_upl"},
                    }
                ],
                "nextPageToken": None,
            },
        ]
        items = list(get_playlist_items("UC123", "upl_pl", "key"))
        assert len(items) == 1
        assert items[0]["playlist_item_id"] == "upl_item"

    @patch("run_youtube._api_get")
    def test_get_comment_threads(self, mock_api_get):
        from run_youtube import get_comment_threads

        mock_api_get.return_value = {
            "items": [
                {
                    "id": "thread1",
                    "snippet": {
                        "videoId": "vid1",
                        "topLevelComment": {
                            "snippet": {
                                "authorDisplayName": "Alice",
                                "authorChannelId": {"value": "UC_alice"},
                                "textOriginal": "Great video!",
                                "likeCount": 10,
                                "publishedAt": "2024-01-01",
                                "updatedAt": "2024-01-01",
                            }
                        },
                        "totalReplyCount": 1,
                        "isPublic": True,
                        "canReply": True,
                    },
                    "replies": {
                        "comments": [
                            {
                                "id": "reply1",
                                "snippet": {
                                    "authorDisplayName": "Bob",
                                    "textOriginal": "Thanks!",
                                    "likeCount": 2,
                                    "publishedAt": "2024-01-02",
                                    "updatedAt": "2024-01-02",
                                },
                            }
                        ]
                    },
                }
            ],
            "nextPageToken": None,
        }

        threads = list(get_comment_threads("UC123", "key"))
        assert len(threads) == 1
        assert threads[0]["comment_id"] == "thread1"
        assert threads[0]["author_display_name"] == "Alice"
        assert len(threads[0]["replies"]) == 1
        assert threads[0]["replies"][0]["author_display_name"] == "Bob"

    @patch("run_youtube._api_get")
    def test_get_channel_sections(self, mock_api_get):
        from run_youtube import get_channel_sections

        mock_api_get.return_value = {
            "items": [
                {
                    "id": "sec1",
                    "snippet": {"type": "singlePlaylist", "title": "Section Title", "position": 0},
                    "contentDetails": {"playlists": ["pl1"], "channels": []},
                }
            ]
        }

        sections = list(get_channel_sections("UC123", "key"))
        assert len(sections) == 1
        assert sections[0]["section_id"] == "sec1"
        assert sections[0]["title"] == "Section Title"

    @patch("run_youtube._api_get")
    def test_get_video_categories(self, mock_api_get):
        from run_youtube import get_video_categories

        mock_api_get.return_value = {
            "items": [
                {
                    "id": "cat1",
                    "snippet": {"title": "Music", "assignable": True, "channelId": "UC_yt"},
                }
            ]
        }

        categories = list(get_video_categories("key"))
        assert len(categories) == 1
        assert categories[0]["category_id"] == "cat1"
        assert categories[0]["title"] == "Music"

    @patch("run_youtube._enumerate_uploads")
    @patch("run_youtube._api_get")
    def test_get_captions(self, mock_api_get, mock_enumerate):
        from run_youtube import get_captions

        mock_enumerate.return_value = ["vid1"]
        mock_api_get.return_value = {
            "items": [
                {
                    "id": "cap1",
                    "snippet": {
                        "videoId": "vid1",
                        "language": "en",
                        "name": "English",
                        "trackKind": "standard",
                        "status": "serving",
                    },
                }
            ]
        }

        captions = list(get_captions("UC123", "uploads_pl", MagicMock()))
        assert len(captions) == 1
        assert captions[0]["caption_id"] == "cap1"
        assert captions[0]["language"] == "en"
