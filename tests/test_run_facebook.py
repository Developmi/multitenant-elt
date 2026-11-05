import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import yaml
from dlt.sources.helpers import requests as dlt_requests

MODULE = "run_facebook"


def _mock_json(data):
    from unittest.mock import MagicMock

    from dlt.sources.helpers import requests

    m = MagicMock(spec=requests.Response)
    m.json.return_value = data
    return m


def _batch_response(core_body, engagement_body):
    return _mock_json(
        [
            {"code": 200, "body": json.dumps(core_body)},
            {"code": 200, "body": json.dumps(engagement_body)},
        ]
    )


def test_page_posts_success(monkeypatch):
    core_body = {
        "data": [
            {
                "id": "post_1",
                "message": "Hello world",
                "created_time": "2024-01-01T12:00:00+0000",
                "permalink_url": "https://fb.com/post_1",
                "story": "Test story",
            }
        ],
        "paging": {"next": None},
    }
    engagement_body = {
        "data": [
            {
                "id": "post_1",
                "likes": {"summary": {"total_count": 10}},
                "comments": {"summary": {"total_count": 5}},
                "shares": {"count": 2},
                "r_like": {"summary": {"total_count": 10}},
                "r_love": {"summary": {"total_count": 0}},
                "r_wow": {"summary": {"total_count": 0}},
                "r_haha": {"summary": {"total_count": 0}},
                "r_sad": {"summary": {"total_count": 0}},
                "r_angry": {"summary": {"total_count": 0}},
            }
        ],
        "paging": {"next": None},
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.post",
        lambda url, params=None: _batch_response(core_body, engagement_body),
    )

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    posts = list(source.resources["page_posts"])

    assert len(posts) == 1
    assert posts[0]["post_id"] == "post_1"
    assert posts[0]["message"] == "Hello world"
    assert posts[0]["likes_count"] == 10
    assert posts[0]["comments_count"] == 5
    assert posts[0]["shares_count"] == 2
    assert posts[0]["story"] == "Test story"
    assert posts[0]["permalink_url"] == "https://fb.com/post_1"
    assert posts[0]["created_time"] == "2024-01-01T12:00:00+0000"


def test_page_feed_success(monkeypatch):
    core_body = {
        "data": [
            {
                "id": "feed_1",
                "message": "Feed post content",
                "created_time": "2024-01-02T14:00:00+0000",
                "permalink_url": "https://fb.com/feed_1",
                "story": "Feed story",
                "from": {"id": "page_123", "name": "Test Page"},
            }
        ],
        "paging": {"next": None},
    }
    engagement_body = {
        "data": [
            {
                "id": "feed_1",
                "likes": {"summary": {"total_count": 20}},
                "comments": {"summary": {"total_count": 7}},
                "shares": {"count": 3},
                "r_like": {"summary": {"total_count": 20}},
                "r_love": {"summary": {"total_count": 0}},
                "r_wow": {"summary": {"total_count": 0}},
                "r_haha": {"summary": {"total_count": 0}},
                "r_sad": {"summary": {"total_count": 0}},
                "r_angry": {"summary": {"total_count": 0}},
            }
        ],
        "paging": {"next": None},
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.post",
        lambda url, params=None: _batch_response(core_body, engagement_body),
    )

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    feed = list(source.resources["feed"])

    assert len(feed) == 1
    assert feed[0]["feed_item_id"] == "feed_1"
    assert feed[0]["message"] == "Feed post content"
    assert feed[0]["author_id"] == "page_123"
    assert feed[0]["author_name"] == "Test Page"
    assert feed[0]["likes_count"] == 20
    assert feed[0]["comments_count"] == 7
    assert feed[0]["shares_count"] == 3
    assert feed[0]["story"] == "Feed story"
    assert feed[0]["permalink_url"] == "https://fb.com/feed_1"


def test_page_insights_success(monkeypatch):
    insights_response = {
        "data": [
            {
                "name": "page_total_media_view_unique",
                "period": "day",
                "values": [{"value": 300, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_media_view",
                "period": "day",
                "values": [{"value": 500, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_video_views",
                "period": "day",
                "values": [{"value": 50, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_views_total",
                "period": "day",
                "values": [{"value": 200, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_daily_follows",
                "period": "day",
                "values": [{"value": 5, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_total_actions",
                "period": "day",
                "values": [{"value": 2, "end_time": "2024-01-01T08:00:00+0000"}],
            },
        ]
    }

    captured_params = {}

    def mock_get(url, params=None):
        captured_params.update(params or {})
        return _mock_json(insights_response)

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    rows = list(source.resources["page_insights_daily"])

    assert len(rows) == 1
    assert rows[0]["report_date"] == "2024-01-01"
    assert rows[0]["page_total_media_view_unique"] == 300
    assert rows[0]["page_media_view"] == 500
    assert rows[0]["page_video_views"] == 50
    assert rows[0]["page_views_total"] == 200
    assert rows[0]["page_daily_follows"] == 5
    assert rows[0]["page_total_actions"] == 2

    assert "since" in captured_params
    assert "until" in captured_params


def test_rate_limit_retry(monkeypatch):
    calls = []

    def mock_post(url, params=None):
        calls.append(1)
        if len(calls) == 1:
            return _mock_json({"error": {"code": 4, "message": "Rate limit"}})
        return _batch_response(
            {
                "data": [
                    {
                        "id": "post_1",
                        "message": "ok",
                    }
                ],
                "paging": {"next": None},
            },
            {
                "data": [
                    {
                        "id": "post_1",
                        "likes": {"summary": {"total_count": 0}},
                        "comments": {"summary": {"total_count": 0}},
                        "r_like": {"summary": {"total_count": 0}},
                        "r_love": {"summary": {"total_count": 0}},
                        "r_wow": {"summary": {"total_count": 0}},
                        "r_haha": {"summary": {"total_count": 0}},
                        "r_sad": {"summary": {"total_count": 0}},
                        "r_angry": {"summary": {"total_count": 0}},
                    }
                ],
                "paging": {"next": None},
            },
        )

    monkeypatch.setattr(f"{MODULE}.requests.post", mock_post)
    monkeypatch.setattr(f"{MODULE}.time.sleep", lambda s: None)

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    posts = list(source.resources["page_posts"])

    assert len(posts) == 1
    assert posts[0]["post_id"] == "post_1"
    assert len(calls) == 2


def test_token_expired(monkeypatch):
    error_data = {"error": {"code": 190, "message": "Token expired"}}
    monkeypatch.setattr(f"{MODULE}.requests.post", lambda url, params=None: _mock_json(error_data))

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    with pytest.raises(Exception, match="Invalid or expired token"):
        list(source.resources["page_posts"])


def test_pagination(monkeypatch):
    responses = [
        _batch_response(
            {
                "data": [
                    {
                        "id": "post_1",
                        "message": "first page",
                    }
                ],
                "paging": {
                    "next": (
                        "https://graph.facebook.com/v25.0/test_page_123/posts"
                        "?fields=...&limit=50&after=cursor2"
                    )
                },
            },
            {
                "data": [
                    {
                        "id": "post_1",
                        "likes": {"summary": {"total_count": 0}},
                        "comments": {"summary": {"total_count": 0}},
                        "r_like": {"summary": {"total_count": 0}},
                        "r_love": {"summary": {"total_count": 0}},
                        "r_wow": {"summary": {"total_count": 0}},
                        "r_haha": {"summary": {"total_count": 0}},
                        "r_sad": {"summary": {"total_count": 0}},
                        "r_angry": {"summary": {"total_count": 0}},
                    }
                ],
                "paging": {"next": None},
            },
        ),
        _batch_response(
            {
                "data": [
                    {
                        "id": "post_2",
                        "message": "second page",
                    }
                ],
                "paging": {"next": None},
            },
            {
                "data": [
                    {
                        "id": "post_2",
                        "likes": {"summary": {"total_count": 0}},
                        "comments": {"summary": {"total_count": 0}},
                        "r_like": {"summary": {"total_count": 0}},
                        "r_love": {"summary": {"total_count": 0}},
                        "r_wow": {"summary": {"total_count": 0}},
                        "r_haha": {"summary": {"total_count": 0}},
                        "r_sad": {"summary": {"total_count": 0}},
                        "r_angry": {"summary": {"total_count": 0}},
                    }
                ],
                "paging": {"next": None},
            },
        ),
    ]

    monkeypatch.setattr(f"{MODULE}.requests.post", lambda url, params=None: responses.pop(0))
    monkeypatch.setattr(f"{MODULE}.time.sleep", lambda s: None)

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    posts = list(source.resources["page_posts"])

    assert len(posts) == 2
    assert posts[0]["message"] == "first page"
    assert posts[1]["message"] == "second page"


def test_connector_key_and_field():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    fb_cfg = template["connectors"]["facebook"]
    assert "page_id" in fb_cfg
    assert "token_env" in fb_cfg


def test_page_posts_reaction_fields(monkeypatch):
    core_body = {
        "data": [
            {
                "id": "post_r1",
                "message": "Reaction test",
                "created_time": "2024-06-01T12:00:00+0000",
                "permalink_url": "https://fb.com/post_r1",
                "story": "Test story",
                "full_picture": "https://fb.com/pic.jpg",
                "status_type": "added_photos",
                "is_published": True,
                "updated_time": "2024-06-01T13:00:00+0000",
            }
        ],
        "paging": {"next": None},
    }
    engagement_body = {
        "data": [
            {
                "id": "post_r1",
                "r_like": {"summary": {"total_count": 42}},
                "r_love": {"summary": {"total_count": 7}},
                "r_wow": {"summary": {"total_count": 3}},
                "r_haha": {"summary": {"total_count": 5}},
                "r_sad": {"summary": {"total_count": 1}},
                "r_angry": {"summary": {"total_count": 0}},
                "likes": {"summary": {"total_count": 42}},
                "comments": {"summary": {"total_count": 5}},
                "shares": {"count": 2},
            }
        ],
        "paging": {"next": None},
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.post",
        lambda url, params=None: _batch_response(core_body, engagement_body),
    )

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    posts = list(source.resources["page_posts"])

    assert len(posts) == 1
    post = posts[0]
    assert post["full_picture"] == "https://fb.com/pic.jpg"
    assert post["r_like"] == 42
    assert post["r_love"] == 7
    assert post["r_wow"] == 3
    assert post["r_haha"] == 5
    assert post["r_sad"] == 1
    assert post["r_angry"] == 0
    assert post["status_type"] == "added_photos"
    assert post["is_published"] is True
    assert post["updated_time"] == "2024-06-01T13:00:00+0000"


def test_page_posts_reactions_zero(monkeypatch):
    core_body = {
        "data": [
            {
                "id": "post_z1",
                "message": "Zero reactions",
                "created_time": "2024-06-02T12:00:00+0000",
                "permalink_url": "https://fb.com/post_z1",
                "status_type": None,
                "is_published": False,
                "updated_time": None,
                "full_picture": None,
            }
        ],
        "paging": {"next": None},
    }
    engagement_body = {
        "data": [
            {
                "id": "post_z1",
                "r_like": {"summary": {"total_count": 0}},
                "r_love": {"summary": {"total_count": 0}},
                "r_wow": {"summary": {"total_count": 0}},
                "r_haha": {"summary": {"total_count": 0}},
                "r_sad": {"summary": {"total_count": 0}},
                "r_angry": {"summary": {"total_count": 0}},
                "likes": {"summary": {"total_count": 0}},
                "comments": {"summary": {"total_count": 0}},
            }
        ],
        "paging": {"next": None},
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.post",
        lambda url, params=None: _batch_response(core_body, engagement_body),
    )

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    posts = list(source.resources["page_posts"])

    assert len(posts) == 1
    post = posts[0]
    assert post["r_like"] == 0
    assert post["r_love"] == 0
    assert post["r_wow"] == 0
    assert post["r_haha"] == 0
    assert post["r_sad"] == 0
    assert post["r_angry"] == 0
    assert post["full_picture"] is None
    assert post["status_type"] is None
    assert post["is_published"] is False
    assert post["updated_time"] is None


def test_page_feed_reaction_fields(monkeypatch):
    core_body = {
        "data": [
            {
                "id": "feed_r1",
                "message": "Feed reaction test",
                "created_time": "2024-06-01T14:00:00+0000",
                "permalink_url": "https://fb.com/feed_r1",
                "story": "Feed story",
                "from": {"id": "page_123", "name": "Test Page"},
                "full_picture": "https://fb.com/feed_pic.jpg",
                "status_type": "added_video",
                "is_published": True,
                "updated_time": "2024-06-01T15:00:00+0000",
            }
        ],
        "paging": {"next": None},
    }
    engagement_body = {
        "data": [
            {
                "id": "feed_r1",
                "r_like": {"summary": {"total_count": 15}},
                "r_love": {"summary": {"total_count": 3}},
                "r_wow": {"summary": {"total_count": 1}},
                "r_haha": {"summary": {"total_count": 2}},
                "r_sad": {"summary": {"total_count": 0}},
                "r_angry": {"summary": {"total_count": 0}},
                "likes": {"summary": {"total_count": 15}},
                "comments": {"summary": {"total_count": 3}},
                "shares": {"count": 1},
            }
        ],
        "paging": {"next": None},
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.post",
        lambda url, params=None: _batch_response(core_body, engagement_body),
    )

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    feed = list(source.resources["feed"])

    assert len(feed) == 1
    item = feed[0]
    assert item["author_id"] == "page_123"
    assert item["author_name"] == "Test Page"
    assert item["full_picture"] == "https://fb.com/feed_pic.jpg"
    assert item["r_like"] == 15
    assert item["r_love"] == 3
    assert item["r_wow"] == 1
    assert item["r_haha"] == 2
    assert item["r_sad"] == 0
    assert item["r_angry"] == 0
    assert item["status_type"] == "added_video"
    assert item["is_published"] is True
    assert item["updated_time"] == "2024-06-01T15:00:00+0000"


def test_page_insights_new_metrics(monkeypatch):
    _v = [{"value": 300, "end_time": "2024-01-01T08:00:00+0000"}]
    _v5 = [{"value": 5, "end_time": "2024-01-01T08:00:00+0000"}]
    _v2 = [{"value": 2, "end_time": "2024-01-01T08:00:00+0000"}]
    insights_response = {
        "data": [
            {"name": "page_total_media_view_unique", "period": "day", "values": _v},
            {
                "name": "page_media_view",
                "period": "day",
                "values": [{"value": 500, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_video_views",
                "period": "day",
                "values": [{"value": 50, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_views_total",
                "period": "day",
                "values": [{"value": 200, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {"name": "page_daily_follows", "period": "day", "values": _v5},
            {"name": "page_total_actions", "period": "day", "values": _v2},
            {
                "name": "page_follows",
                "period": "day",
                "values": [{"value": 150, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_post_engagements",
                "period": "day",
                "values": [{"value": 89, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_daily_follows_unique",
                "period": "day",
                "values": [{"value": 12, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_daily_unfollows_unique",
                "period": "day",
                "values": [{"value": 3, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_actions_post_reactions_like_total",
                "period": "day",
                "values": [{"value": 30, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_actions_post_reactions_love_total",
                "period": "day",
                "values": [{"value": 10, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_actions_post_reactions_wow_total",
                "period": "day",
                "values": [{"value": 5, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_actions_post_reactions_haha_total",
                "period": "day",
                "values": [{"value": 8, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_actions_post_reactions_sorry_total",
                "period": "day",
                "values": [{"value": 2, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_actions_post_reactions_anger_total",
                "period": "day",
                "values": [{"value": 1, "end_time": "2024-01-01T08:00:00+0000"}],
            },
        ]
    }

    mock_get = lambda url, params=None: _mock_json(insights_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    rows = list(source.resources["page_insights_daily"])

    assert len(rows) == 1
    row = rows[0]
    assert row["page_follows"] == 150
    assert row["page_post_engagements"] == 89
    assert row["page_daily_follows_unique"] == 12
    assert row["page_daily_unfollows_unique"] == 3
    assert row["page_actions_post_reactions_like_total"] == 30
    assert row["page_actions_post_reactions_love_total"] == 10
    assert row["page_actions_post_reactions_wow_total"] == 5
    assert row["page_actions_post_reactions_haha_total"] == 8
    assert row["page_actions_post_reactions_sorry_total"] == 2
    assert row["page_actions_post_reactions_anger_total"] == 1


def test_page_profile_daily_guard_skips_same_day(monkeypatch):
    today = datetime.now(timezone.utc).date().isoformat()

    class FakeDltCurrent:
        _state = {"last_run": today}

        def resource_state(self):
            return self._state

    monkeypatch.setattr(f"{MODULE}.dlt.current", FakeDltCurrent())

    profile_response = {
        "id": "page_123",
        "fan_count": 1000,
        "followers_count": 800,
        "name": "Test Page",
        "username": "testpage",
        "picture": {"url": "https://fb.com/pic.jpg"},
        "about": "Test about",
        "website": "https://test.com",
        "verification_status": "not_verified",
        "rating_count": 42,
        "category": "Business",
        "cover": {"source": "https://fb.com/cover.jpg"},
    }

    mock_get = lambda url, params=None: _mock_json(profile_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_facebook import get_page_profile

    resource = get_page_profile("test_page_123", "mock_token")
    results = list(resource)

    assert len(results) == 0, "Expected no yield when already fetched today"


def test_page_profile_daily_guard_fetches_new_day(monkeypatch):
    class FakeDltCurrent:
        _state = {"last_run": "2026-07-27"}  # yesterday

        def resource_state(self):
            return self._state

    monkeypatch.setattr(f"{MODULE}.dlt.current", FakeDltCurrent())

    profile_response = {
        "id": "page_123",
        "fan_count": 1000,
        "followers_count": 800,
        "name": "Test Page",
        "username": "testpage",
        "picture": {"url": "https://fb.com/pic.jpg"},
        "about": "Test about",
        "website": "https://test.com",
        "verification_status": "not_verified",
        "rating_count": 42,
        "category": "Business",
        "cover": {"source": "https://fb.com/cover.jpg"},
    }

    mock_get = lambda url, params=None: _mock_json(profile_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_facebook import get_page_profile

    resource = get_page_profile("test_page_123", "mock_token")
    results = list(resource)

    assert len(results) == 1
    profile = results[0]
    assert profile["page_id"] == "page_123"
    assert profile["fan_count"] == 1000
    assert profile["followers_count"] == 800
    assert profile["name"] == "Test Page"
    assert profile["username"] == "testpage"
    assert profile["picture_url"] == "https://fb.com/pic.jpg"
    assert profile["about"] == "Test about"
    assert profile["website"] == "https://test.com"
    assert profile["verification_status"] == "not_verified"
    assert profile["rating_count"] == 42
    assert profile["category"] == "Business"
    assert profile["cover"] == "https://fb.com/cover.jpg"


def test_connector_insights_days_back():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    fb_cfg = template["connectors"]["facebook"]
    assert "insights_days_back" in fb_cfg
    assert isinstance(fb_cfg["insights_days_back"], int)


def test_page_profile_resource_declares_column_hints():
    """YLD-S1 (SDD-F WU3): page_profile hints keep the raw schema stable.

    dlt only materializes columns that receive data; all-NULL profile fields
    (cover/about on an unconfigured page, etc.) would be dropped from the raw
    table without explicit ``columns=`` hints (obs #625 bug class, IG WU5
    precedent run_instagram.py:466-479). The hints declare the volatile fields
    text/bigint-nullable so replace always creates them.
    """
    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    profile = source.resources["page_profile"]
    hints = {name: col for name, col in (profile.columns or {}).items()}
    expected = {
        "picture_url": "text",
        "about": "text",
        "website": "text",
        "category": "text",
        "cover": "text",
        "rating_count": "bigint",
    }
    for column, data_type in expected.items():
        assert column in hints, f"column {column} needs a dlt nullable hint"
        assert hints[column]["data_type"] == data_type
        assert hints[column]["nullable"] is True


def test_should_retry_branches():
    from run_facebook import _should_retry

    assert _should_retry({}) == (False, None)

    # Rate limit with string wait
    retry, wait = _should_retry({"error": {"code": 80000, "error_user_title": "str_wait"}})
    assert retry is True
    assert wait == 60

    # Rate limit with subcode
    retry, wait = _should_retry({"error": {"code": 4, "error_subcode": 15}})
    assert retry is True
    assert wait == 15

    # Code 190
    with pytest.raises(Exception, match="Invalid or expired token"):
        _should_retry({"error": {"code": 190, "message": "Expired"}})

    # Other code
    with pytest.raises(Exception, match="API error 500"):
        _should_retry({"error": {"code": 500, "message": "Internal"}})


def test_execute_batch_error_branches():
    from run_facebook import _execute_batch

    # HTTPError 429
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.json.return_value = {"error": {"code": 4, "message": "Rate limited"}}
    http_err = dlt_requests.HTTPError(response=mock_resp)

    with patch("run_facebook.requests.post", side_effect=http_err):
        with patch("run_facebook.time.sleep"):
            with pytest.raises(Exception, match="Batch rate limit exceeded"):
                _execute_batch([{"method": "GET", "relative_url": "test"}], "tok")

    # HTTPError 190
    mock_resp.status_code = 400
    mock_resp.json.return_value = {"error": {"code": 190, "message": "Expired"}}
    http_err_190 = dlt_requests.HTTPError(response=mock_resp)
    with patch("run_facebook.requests.post", side_effect=http_err_190):
        with pytest.raises(Exception, match="Invalid or expired token"):
            _execute_batch([{"method": "GET", "relative_url": "test"}], "tok")

    # RequestException
    with patch(
        "run_facebook.requests.post", side_effect=dlt_requests.RequestException("Conn drop")
    ):
        with patch("run_facebook.time.sleep"):
            with pytest.raises(Exception, match="Batch request failed after"):
                _execute_batch([{"method": "GET", "relative_url": "test"}], "tok")


def test_do_request_branches():
    from run_facebook import _do_request, _get_reaction

    # _get_reaction flat
    assert _get_reaction({"r_like": 42}, "r_like") == 42
    assert _get_reaction({}, "r_like") == 0

    # HTTP 500 reduce limit
    mock_resp_500 = MagicMock()
    mock_resp_500.status_code = 500
    mock_resp_500.json.return_value = {"error": {"message": "Please reduce the amount of data"}}
    err_500 = dlt_requests.HTTPError(response=mock_resp_500)

    mock_resp_ok = MagicMock()
    mock_resp_ok.json.return_value = {"data": [{"id": "ok"}]}

    with patch("run_facebook.requests.get", side_effect=[err_500, mock_resp_ok]):
        with patch("run_facebook.time.sleep"):
            params = {"limit": 50}
            data = _do_request("http://fb.test", params, "test_ctx", retries=2)
            assert data == {"data": [{"id": "ok"}]}
            assert params["limit"] == 25

    # HTTP code 100 invalid parameter
    mock_resp_100 = MagicMock()
    mock_resp_100.status_code = 400
    mock_resp_100.json.return_value = {"error": {"code": 100, "message": "Invalid param"}}
    err_100 = dlt_requests.HTTPError(response=mock_resp_100)
    with patch("run_facebook.requests.get", side_effect=err_100):
        with pytest.raises(Exception, match="Invalid parameter"):
            _do_request("http://fb.test", {}, "ctx", retries=1)

    # HTTP code 190 expired token
    mock_resp_190 = MagicMock()
    mock_resp_190.status_code = 400
    mock_resp_190.json.return_value = {"error": {"code": 190, "message": "Expired"}}
    err_190 = dlt_requests.HTTPError(response=mock_resp_190)
    with patch("run_facebook.requests.get", side_effect=err_190):
        with pytest.raises(Exception, match="Token expired"):
            _do_request("http://fb.test", {}, "ctx", retries=1)

    # HTTP 429 rate limit exceeded
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.json.return_value = {"error": {"code": 4, "message": "Rate limit"}}
    err_429 = dlt_requests.HTTPError(response=mock_resp_429)
    with patch("run_facebook.requests.get", side_effect=err_429):
        with patch("run_facebook.time.sleep"):
            with pytest.raises(Exception, match="Rate limit exceeded"):
                _do_request("http://fb.test", {}, "ctx", retries=1)

    # HTTP generic error 503
    mock_resp_503 = MagicMock()
    mock_resp_503.status_code = 503
    mock_resp_503.json.return_value = {"error": {"code": 503, "message": "Unavailable"}}
    err_503 = dlt_requests.HTTPError(response=mock_resp_503)
    with patch("run_facebook.requests.get", side_effect=err_503):
        with pytest.raises(Exception, match="API error: Unavailable"):
            _do_request("http://fb.test", {}, "ctx", retries=1)

    # RequestException retry and raise
    with patch("run_facebook.requests.get", side_effect=dlt_requests.RequestException("Drop")):
        with patch("run_facebook.time.sleep"):
            with pytest.raises(dlt_requests.RequestException):
                _do_request("http://fb.test", {}, "ctx", retries=1)

    # Data with retry code exceeded
    with patch(
        "run_facebook.requests.get", return_value=MagicMock(json=lambda: {"error": {"code": 4}})
    ):
        with patch("run_facebook.time.sleep"):
            with pytest.raises(Exception, match="Rate limit exceeded"):
                _do_request("http://fb.test", {}, "ctx", retries=1)


def test_fetch_batch_posts_errors():
    from run_facebook import _fetch_page_batch

    # Results not a list or < 2 items
    with patch("run_facebook._execute_batch", return_value={"bad": "format"}):
        with pytest.raises(Exception, match="Batch returned unexpected format"):
            _fetch_page_batch("posts", "p1", "tok")

    # Sub-request code != 200
    with patch(
        "run_facebook._execute_batch",
        return_value=[{"code": 400, "body": "err"}, {"code": 200, "body": "{}"}],
    ):
        with pytest.raises(Exception, match="Batch sub-request 0 failed"):
            _fetch_page_batch("posts", "p1", "tok")

    # Sub-request invalid json body
    with patch(
        "run_facebook._execute_batch",
        return_value=[{"code": 200, "body": "invalid_json"}, {"code": 200, "body": "{}"}],
    ):
        with pytest.raises(Exception, match="parse error"):
            _fetch_page_batch("posts", "p1", "tok")


def test_facebook_cli_main(monkeypatch, tmp_path):
    import runpy
    import sys

    # 1. Client file not found -> exit(1)
    monkeypatch.setattr(sys, "argv", ["run_facebook", "--client", "nonexistent"])
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(tmp_path / "absent.yml")
    ):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path("src/connectors/run_facebook.py", run_name="__main__")
        assert exc.value.code == 1

    # 2. Inactive client -> exit(0)
    fake_client_file = tmp_path / "client.yml"
    fake_client_file.write_text("active: false\n")
    monkeypatch.setattr(sys, "argv", ["run_facebook", "--client", "inactive_client"])
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(fake_client_file)
    ):
        with patch("agency_analytics.client_config.load_client", return_value={"active": False}):
            with pytest.raises(SystemExit) as exc:
                runpy.run_path("src/connectors/run_facebook.py", run_name="__main__")
            assert exc.value.code == 0

    # 3. Validation errors -> exit(1)
    monkeypatch.setattr(sys, "argv", ["run_facebook", "--client", "invalid_client"])
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(fake_client_file)
    ):
        with patch("agency_analytics.client_config.load_client", return_value={"active": True}):
            with patch(
                "agency_analytics.client_config.validate_client", return_value=["Some error"]
            ):
                with pytest.raises(SystemExit) as exc:
                    runpy.run_path("src/connectors/run_facebook.py", run_name="__main__")
                assert exc.value.code == 1

    # 4. Connector not enabled -> exit(0)
    monkeypatch.setattr(sys, "argv", ["run_facebook", "--client", "disabled_client"])
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(fake_client_file)
    ):
        with patch(
            "agency_analytics.client_config.load_client",
            return_value={"active": True, "connectors": {"facebook": {"enabled": False}}},
        ):
            with patch("agency_analytics.client_config.validate_client", return_value=[]):
                with pytest.raises(SystemExit) as exc:
                    runpy.run_path("src/connectors/run_facebook.py", run_name="__main__")
                assert exc.value.code == 0

    # 5. Missing envs -> exit(1)
    monkeypatch.setattr(sys, "argv", ["run_facebook", "--client", "missing_env_client"])
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(fake_client_file)
    ):
        with patch(
            "agency_analytics.client_config.load_client",
            return_value={"active": True, "connectors": {"facebook": {"enabled": True}}},
        ):
            with patch("agency_analytics.client_config.validate_client", return_value=[]):
                with patch(
                    "agency_analytics.client_config.missing_envs", return_value=["MISSING_KEY"]
                ):
                    with pytest.raises(SystemExit) as exc:
                        runpy.run_path("src/connectors/run_facebook.py", run_name="__main__")
                    assert exc.value.code == 1

    # 6. Success run
    monkeypatch.setattr(sys, "argv", ["run_facebook", "--client", "good_client"])
    monkeypatch.setenv("FB_TOKEN", "valid_tok")
    client_cfg = {
        "active": True,
        "connectors": {
            "facebook": {
                "enabled": True,
                "page_id": "p123",
                "token_env": "FB_TOKEN",
                "insights_days_back": 30,
            }
        },
    }
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = "pipeline_ok"
    with patch(
        "agency_analytics.client_config.client_file_path", return_value=str(fake_client_file)
    ):
        with patch("agency_analytics.client_config.load_client", return_value=client_cfg):
            with patch("agency_analytics.client_config.validate_client", return_value=[]):
                with patch("agency_analytics.client_config.missing_envs", return_value=[]):
                    with patch("dlt.pipeline", return_value=mock_pipeline):
                        with patch(
                            "run_facebook.facebook_page_source", return_value="dummy_source"
                        ):
                            runpy.run_path("src/connectors/run_facebook.py", run_name="__main__")
                            mock_pipeline.run.assert_called_once()
