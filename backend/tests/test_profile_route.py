"""Tests for POST /api/profile/fetch.

We patch ``app.routes.profile.fetch_profile_and_posts`` so the route
exercises its own validation, error mapping, and session creation
without touching the network or the heavyweight pipeline modules.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.profile import router as profile_router
from app.schemas.profile import InstagramPost, InstagramProfile
from app.services.instaloader_fetch import (
    FetchResult,
    InstaProfileNotFound,
    InstaRateLimited,
)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(profile_router, prefix="/api")
    return TestClient(app)


def _public_result(username: str = "instagram") -> FetchResult:
    profile = InstagramProfile(
        username=username,
        full_name="Test User",
        biography="hello",
        followers=1000,
        followees=10,
        is_private=False,
        is_verified=False,
        external_url=None,
        profile_pic_url="https://example.com/p.jpg",
        post_count_total=2,
    )
    posts = [
        InstagramPost(
            shortcode="AAA",
            image_url="https://example.com/a.jpg",
            caption="hi",
            location_name=None,
            location_id=None,
            taken_at_iso="2024-01-01T00:00:00+00:00",
            is_video=False,
            tagged_users=[],
        )
    ]
    return FetchResult(profile=profile, posts=posts)


def _private_result(username: str = "locked") -> FetchResult:
    profile = InstagramProfile(
        username=username,
        full_name="Locked Acct",
        biography="",
        followers=10,
        followees=5,
        is_private=True,
        is_verified=False,
        external_url=None,
        profile_pic_url="https://example.com/p.jpg",
        post_count_total=42,
    )
    return FetchResult(profile=profile, posts=[])


# ---------- happy path ----------


def test_fetch_returns_session_and_profile(client, monkeypatch):
    async def fake_fetch(username: str, max_posts: int = 50) -> FetchResult:
        assert username == "instagram"
        return _public_result()

    monkeypatch.setattr("app.routes.profile.fetch_profile_and_posts", fake_fetch)

    resp = client.post("/api/profile/fetch", json={"username": "Instagram"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"]
    assert body["profile"]["username"] == "instagram"
    assert body["profile"]["is_private"] is False
    assert body["post_count"] == 1


def test_fetch_normalizes_at_prefix_and_lowercases(client, monkeypatch):
    seen = {}

    async def fake_fetch(username: str, max_posts: int = 50) -> FetchResult:
        seen["username"] = username
        return _public_result(username)

    monkeypatch.setattr("app.routes.profile.fetch_profile_and_posts", fake_fetch)

    resp = client.post("/api/profile/fetch", json={"username": "@MixedCaseName"})
    assert resp.status_code == 200, resp.text
    assert seen["username"] == "mixedcasename"


# ---------- validation ----------


@pytest.mark.parametrize(
    "username",
    [
        "",                    # empty
        "x" * 31,              # too long
        "has space",           # space
        "bad-char",            # hyphen not allowed
        "weird!chars",         # punctuation
    ],
)
def test_fetch_rejects_invalid_username(client, monkeypatch, username):
    async def fake_fetch(*_a, **_kw):  # pragma: no cover — must not be called
        raise AssertionError("fetcher should not run for invalid input")

    monkeypatch.setattr("app.routes.profile.fetch_profile_and_posts", fake_fetch)

    resp = client.post("/api/profile/fetch", json={"username": username})
    assert resp.status_code == 422, resp.text


# ---------- upstream error mapping ----------


def test_fetch_404_when_profile_not_found(client, monkeypatch):
    async def fake_fetch(*_a, **_kw):
        raise InstaProfileNotFound("nope")

    monkeypatch.setattr("app.routes.profile.fetch_profile_and_posts", fake_fetch)

    resp = client.post("/api/profile/fetch", json={"username": "ghost"})
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


def test_fetch_429_when_rate_limited(client, monkeypatch):
    async def fake_fetch(*_a, **_kw):
        raise InstaRateLimited("slow down")

    monkeypatch.setattr("app.routes.profile.fetch_profile_and_posts", fake_fetch)

    resp = client.post("/api/profile/fetch", json={"username": "anyone"})
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "60"


def test_fetch_403_when_profile_is_private(client, monkeypatch):
    async def fake_fetch(*_a, **_kw):
        return _private_result()

    monkeypatch.setattr("app.routes.profile.fetch_profile_and_posts", fake_fetch)

    resp = client.post("/api/profile/fetch", json={"username": "locked"})
    assert resp.status_code == 403
    assert "private" in resp.json()["detail"].lower()


def test_fetch_403_when_fetcher_raises_private(client, monkeypatch):
    """Cover the path where the fetcher itself signals private (vs the
    post-fetch is_private guard)."""
    from app.services.instaloader_fetch import InstaProfilePrivate

    async def fake_fetch(*_a, **_kw):
        raise InstaProfilePrivate("locked")

    monkeypatch.setattr("app.routes.profile.fetch_profile_and_posts", fake_fetch)

    resp = client.post("/api/profile/fetch", json={"username": "locked"})
    assert resp.status_code == 403
    assert "private" in resp.json()["detail"].lower()


def test_fetch_500_when_fetcher_raises_unknown(client, monkeypatch):
    async def fake_fetch(*_a, **_kw):
        raise RuntimeError("something exploded")

    monkeypatch.setattr("app.routes.profile.fetch_profile_and_posts", fake_fetch)

    resp = client.post("/api/profile/fetch", json={"username": "anyone"})
    assert resp.status_code == 500


# ---------- session state seeded for the audit pipeline ----------


def test_session_state_is_seeded_for_audit(client, monkeypatch):
    async def fake_fetch(*_a, **_kw):
        return _public_result()

    monkeypatch.setattr("app.routes.profile.fetch_profile_and_posts", fake_fetch)

    resp = client.post("/api/profile/fetch", json={"username": "instagram"})
    assert resp.status_code == 200
    sid = resp.json()["session_id"]

    from app.session_store import get_session

    session = get_session(sid)
    assert session is not None
    assert session.data["profile"]["username"] == "instagram"
    assert isinstance(session.data["posts"], list)
    assert session.data["audit_started"] is False
    assert session.data["findings"] == []
    assert session.data["pipeline_outcomes"] == {}
