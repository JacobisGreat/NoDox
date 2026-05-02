"""Unit tests for the anonymous Instagram fetcher.

The fetcher constructs ``httpx.Client(...)`` inline. We swap it with a
factory that wires in a ``MockTransport`` so we never hit the network.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services import instaloader_fetch as fetcher
from app.services.instaloader_fetch import (
    FetchResult,
    InstaFetchError,
    InstaProfileNotFound,
    InstaRateLimited,
    fetch_profile_and_posts,
)


def _patch_client(monkeypatch, handler) -> None:
    """Replace httpx.Client *as seen by the fetcher module* with one
    that uses a MockTransport delegating to ``handler``."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def factory(*_args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(fetcher.httpx, "Client", factory)


# ---------- happy path ----------


async def test_fetch_parses_real_profile_payload(
    monkeypatch, ig_profile_json_bytes
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-IG-App-ID"] == "936619743392459"
        assert "username=instagram" in str(request.url)
        return httpx.Response(
            200,
            content=ig_profile_json_bytes,
            headers={"content-type": "application/json"},
        )

    _patch_client(monkeypatch, handler)

    result: FetchResult = await fetch_profile_and_posts("instagram", max_posts=5)

    assert result.profile.username == "instagram"
    assert result.profile.full_name == "Instagram"
    assert result.profile.is_verified is True
    assert result.profile.is_private is False
    assert result.profile.followers > 0
    assert result.profile.post_count_total > 0
    assert result.profile.profile_pic_url.startswith("http")
    # Inline timeline edges should populate posts up to the cap.
    assert 1 <= len(result.posts) <= 5
    first = result.posts[0]
    assert first.shortcode
    assert first.image_url.startswith("http")


async def test_fetch_respects_max_posts_cap(monkeypatch, ig_profile_json_bytes):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ig_profile_json_bytes)

    _patch_client(monkeypatch, handler)

    result = await fetch_profile_and_posts("instagram", max_posts=3)
    assert len(result.posts) == 3


# ---------- error mapping ----------


async def test_fetch_404_raises_not_found(monkeypatch, ig_404_html_bytes):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=ig_404_html_bytes)

    _patch_client(monkeypatch, handler)

    with pytest.raises(InstaProfileNotFound):
        await fetch_profile_and_posts("nope_xxx", max_posts=5)


async def test_fetch_user_null_raises_not_found(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"user": None}})

    _patch_client(monkeypatch, handler)

    with pytest.raises(InstaProfileNotFound):
        await fetch_profile_and_posts("ghost", max_posts=5)


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503])
async def test_fetch_walled_or_rate_limited(monkeypatch, status):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"")

    _patch_client(monkeypatch, handler)

    with pytest.raises(InstaRateLimited):
        await fetch_profile_and_posts("anyone", max_posts=5)


async def test_fetch_unexpected_status_raises_fetch_error(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(418, content=b"")

    _patch_client(monkeypatch, handler)

    with pytest.raises(InstaFetchError):
        await fetch_profile_and_posts("anyone", max_posts=5)


async def test_fetch_invalid_json_raises_fetch_error(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    _patch_client(monkeypatch, handler)

    with pytest.raises(InstaFetchError):
        await fetch_profile_and_posts("anyone", max_posts=5)


async def test_fetch_network_error_raises_fetch_error(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _patch_client(monkeypatch, handler)

    with pytest.raises(InstaFetchError):
        await fetch_profile_and_posts("anyone", max_posts=5)


# ---------- private profile ----------


async def test_fetch_private_profile_returns_no_posts(monkeypatch):
    user = {
        "username": "locked",
        "full_name": "Locked Acct",
        "biography": "shh",
        "edge_followed_by": {"count": 10},
        "edge_follow": {"count": 5},
        "is_private": True,
        "is_verified": False,
        "external_url": None,
        "profile_pic_url": "https://example.com/p.jpg",
        "profile_pic_url_hd": "https://example.com/p_hd.jpg",
        "edge_owner_to_timeline_media": {
            "count": 42,
            "edges": [
                {
                    "node": {
                        "shortcode": "AAA",
                        "display_url": "https://example.com/x.jpg",
                        "is_video": False,
                        "taken_at_timestamp": 1700000000,
                    }
                }
            ],
        },
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"user": user}})

    _patch_client(monkeypatch, handler)

    result = await fetch_profile_and_posts("locked", max_posts=10)
    assert result.profile.is_private is True
    assert result.profile.post_count_total == 42
    # Posts list is intentionally empty for private profiles even when
    # the API echoes back stub edges.
    assert result.posts == []


# ---------- post node parsing ----------


async def test_fetch_skips_video_only_when_required_fields_missing(monkeypatch):
    user = {
        "username": "eg",
        "full_name": "Example",
        "biography": "",
        "edge_followed_by": {"count": 1},
        "edge_follow": {"count": 1},
        "is_private": False,
        "is_verified": False,
        "external_url": None,
        "profile_pic_url": "https://example.com/p.jpg",
        "edge_owner_to_timeline_media": {
            "count": 3,
            "edges": [
                # Missing shortcode → skipped.
                {"node": {"display_url": "https://example.com/a.jpg"}},
                # Missing image → skipped.
                {"node": {"shortcode": "BBB"}},
                # Valid.
                {
                    "node": {
                        "shortcode": "CCC",
                        "display_url": "https://example.com/c.jpg",
                        "is_video": False,
                        "taken_at_timestamp": 1700000000,
                        "edge_media_to_caption": {
                            "edges": [{"node": {"text": "hello world"}}]
                        },
                        "location": {"name": "Paris", "id": "12345"},
                        "edge_media_to_tagged_user": {
                            "edges": [
                                {
                                    "node": {
                                        "user": {"username": "friend1"}
                                    }
                                }
                            ]
                        },
                    }
                },
            ],
        },
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"user": user}})

    _patch_client(monkeypatch, handler)

    result = await fetch_profile_and_posts("eg", max_posts=10)
    assert len(result.posts) == 1
    p = result.posts[0]
    assert p.shortcode == "CCC"
    assert p.caption == "hello world"
    assert p.location_name == "Paris"
    assert p.location_id == 12345
    assert p.tagged_users == ["friend1"]
    assert p.taken_at_iso.startswith("20")


async def test_fetch_uses_hd_pic_when_present(monkeypatch):
    user = {
        "username": "x",
        "full_name": "X",
        "biography": "",
        "edge_followed_by": {"count": 1},
        "edge_follow": {"count": 1},
        "is_private": False,
        "is_verified": False,
        "external_url": None,
        "profile_pic_url": "https://example.com/sd.jpg",
        "profile_pic_url_hd": "https://example.com/hd.jpg",
        "edge_owner_to_timeline_media": {"count": 0, "edges": []},
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"user": user}})

    _patch_client(monkeypatch, handler)

    result = await fetch_profile_and_posts("x", max_posts=5)
    assert result.profile.profile_pic_url == "https://example.com/hd.jpg"
