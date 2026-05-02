"""Tests for the identity pipeline's Sherlock-compatible matcher."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.data.loader import load_platforms, render_url
from app.pipelines.identity import (
    _check_platform,
    _is_claimed,
    _looks_like_waf,
    _WAF_FINGERPRINTS,
)


# --------------------------- _is_claimed unit tests --------------------------- #


class TestIsClaimed:
    def test_status_code_2xx_means_claimed(self):
        platform = {"error_types": ["status_code"], "name": "X"}
        assert _is_claimed(platform, 200, "") is True
        assert _is_claimed(platform, 204, "") is True

    def test_status_code_non_2xx_means_available(self):
        platform = {"error_types": ["status_code"], "name": "X"}
        assert _is_claimed(platform, 404, "") is False
        assert _is_claimed(platform, 301, "") is False
        assert _is_claimed(platform, 500, "") is False

    def test_status_code_with_explicit_error_codes(self):
        platform = {
            "error_types": ["status_code"],
            "error_codes": [404, 410],
            "name": "X",
        }
        assert _is_claimed(platform, 200, "") is True
        assert _is_claimed(platform, 404, "") is False
        assert _is_claimed(platform, 410, "") is False
        # 500 isn't in the explicit error_codes list → claimed.
        assert _is_claimed(platform, 500, "") is True

    def test_message_present_means_available(self):
        platform = {
            "error_types": ["message"],
            "error_messages": ["User Not Found!"],
            "name": "Plurk",
        }
        assert _is_claimed(platform, 200, "...User Not Found!...") is False

    def test_message_absent_means_claimed(self):
        platform = {
            "error_types": ["message"],
            "error_messages": ["User Not Found!"],
            "name": "Plurk",
        }
        assert _is_claimed(platform, 200, "<html>real profile</html>") is True

    def test_message_list_any_match_means_available(self):
        platform = {
            "error_types": ["message"],
            "error_messages": ["foo", "bar", "baz"],
            "name": "X",
        }
        assert _is_claimed(platform, 200, "we have bar in the body") is False
        assert _is_claimed(platform, 200, "we have neither in the body") is True

    def test_response_url_2xx_claimed_else_available(self):
        platform = {"error_types": ["response_url"], "name": "X"}
        assert _is_claimed(platform, 200, "") is True
        # When redirects are disabled, a 30x means user doesn't exist.
        assert _is_claimed(platform, 302, "") is False

    def test_unknown_errortype_treated_as_available(self):
        platform = {"error_types": ["weird"], "name": "X"}
        assert _is_claimed(platform, 200, "") is False

    def test_combined_errortype_any_negative_wins(self):
        platform = {
            "error_types": ["status_code", "message"],
            "error_messages": ["nope"],
            "name": "X",
        }
        # 200 + no error msg → claimed
        assert _is_claimed(platform, 200, "<html>fine</html>") is True
        # 200 but has the error string → available
        assert _is_claimed(platform, 200, "page says nope") is False
        # 404 → available regardless of body
        assert _is_claimed(platform, 404, "page says nope") is False


# --------------------------- WAF fingerprint tests ---------------------------- #


def test_waf_detection_for_known_fingerprints():
    for fingerprint in _WAF_FINGERPRINTS:
        body = f"<html><body>before {fingerprint} after</body></html>"
        assert _looks_like_waf(body) is True


def test_waf_clean_body_passes():
    assert _looks_like_waf("<html>plain page</html>") is False
    assert _looks_like_waf("") is False


# --------------------------- _check_platform with mocked http ----------------- #


def _mock_http(handler) -> httpx.AsyncClient:
    """Build an AsyncClient backed by httpx.MockTransport."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest.fixture
def sema():
    return asyncio.Semaphore(2)


async def test_status_code_404_returns_no_finding(sema):
    platform = {
        "name": "Bluesky",
        "url_template": "https://example.com/{username}",
        "probe_template": "https://example.com/{username}",
        "method": "GET",
        "headers": {},
        "error_types": ["status_code"],
        "error_messages": [],
        "error_codes": [],
        "error_url_template": "",
        "regex_check": None,
        "category": "social_media",
    }

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="ghost",
            full_name="",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert result is None


async def test_status_code_200_emits_low_finding(sema):
    platform = {
        "name": "Bluesky",
        "url_template": "https://example.com/{username}",
        "probe_template": "https://example.com/{username}",
        "method": "GET",
        "headers": {},
        "error_types": ["status_code"],
        "error_messages": [],
        "error_codes": [],
        "error_url_template": "",
        "regex_check": None,
        "category": "social_media",
    }

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>plain</html>")

    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="real",
            full_name="",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert result is not None
    assert result.confidence == 0.30
    assert result.risk_level == "LOW"
    assert "real" in result.metadata["url"]
    assert result.metadata["detection"] == "status_code"


async def test_message_match_means_user_not_found(sema):
    platform = {
        "name": "Plurk",
        "url_template": "https://example.com/{username}",
        "probe_template": "https://example.com/{username}",
        "method": "GET",
        "headers": {},
        "error_types": ["message"],
        "error_messages": ["User Not Found!"],
        "error_codes": [],
        "error_url_template": "",
        "regex_check": None,
        "category": "social_media",
    }

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>User Not Found!</html>")

    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="ghost",
            full_name="",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert result is None


async def test_message_absent_means_user_claimed(sema):
    platform = {
        "name": "Plurk",
        "url_template": "https://example.com/{username}",
        "probe_template": "https://example.com/{username}",
        "method": "GET",
        "headers": {},
        "error_types": ["message"],
        "error_messages": ["User Not Found!"],
        "error_codes": [],
        "error_url_template": "",
        "regex_check": None,
        "category": "social_media",
    }

    # Body must echo the username — message-only Sherlock entries now
    # require a positive signal, not just absence-of-error, to avoid the
    # generic-landing-page false positives (Signal/Discord.bio class).
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="<html>realuser's profile — welcome</html>"
        )

    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="realuser",
            full_name="",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert result is not None
    assert result.confidence == 0.30


async def test_response_url_disables_redirects(sema):
    platform = {
        "name": "Avizo",
        "url_template": "https://example.com/{username}/",
        "probe_template": "https://example.com/{username}/",
        "method": "GET",
        "headers": {},
        "error_types": ["response_url"],
        "error_messages": [],
        "error_codes": [],
        "error_url_template": "https://example.com/",
        "regex_check": None,
        "category": "social_media",
    }
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        # Server tries to redirect missing users to root.
        return httpx.Response(302, headers={"location": "https://example.com/"})

    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="ghost",
            full_name="",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    # Redirects are disabled for response_url; we see the 302 directly,
    # which indicates "user doesn't exist."
    assert result is None
    assert "ghost" in seen["url"]


async def test_response_url_2xx_means_claimed(sema):
    platform = {
        "name": "Avizo",
        "url_template": "https://example.com/{username}/",
        "probe_template": "https://example.com/{username}/",
        "method": "GET",
        "headers": {},
        "error_types": ["response_url"],
        "error_messages": [],
        "error_codes": [],
        "error_url_template": "https://example.com/",
        "regex_check": None,
        "category": "social_media",
    }

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>real user</html>")

    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="realuser",
            full_name="",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert result is not None


async def test_regex_check_skips_request_when_username_invalid(sema):
    platform = {
        "name": "1337x",
        "url_template": "https://example.com/{username}/",
        "probe_template": "https://example.com/{username}/",
        "method": "GET",
        "headers": {},
        "error_types": ["status_code"],
        "error_messages": [],
        "error_codes": [],
        "error_url_template": "",
        "regex_check": r"^[A-Za-z0-9]{4,12}$",
        "category": "social_media",
    }
    called = {"count": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        called["count"] += 1
        return httpx.Response(200)

    # Username has a dot — fails the regex.
    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="bad.user",
            full_name="",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert result is None
    assert called["count"] == 0  # never issued the request


async def test_url_probe_is_hit_but_url_template_is_displayed(sema):
    platform = {
        "name": "Bluesky",
        "url_template": "https://bsky.app/profile/{username}.bsky.social",
        "probe_template": (
            "https://api.example.com/getProfile?actor={username}.bsky.social"
        ),
        "method": "GET",
        "headers": {},
        "error_types": ["status_code"],
        "error_messages": [],
        "error_codes": [],
        "error_url_template": "",
        "regex_check": None,
        "category": "social_media",
    }
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, text="{}")

    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="someone",
            full_name="",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert result is not None
    assert "api.example.com" in seen["url"]
    assert result.metadata["url"] == "https://bsky.app/profile/someone.bsky.social"


async def test_per_site_headers_are_merged(sema):
    platform = {
        "name": "Reddit",
        "url_template": "https://example.com/{username}",
        "probe_template": "https://example.com/{username}",
        "method": "GET",
        "headers": {"accept-language": "en-US,en;q=0.9", "x-custom": "1"},
        "error_types": ["status_code"],
        "error_messages": [],
        "error_codes": [],
        "error_url_template": "",
        "regex_check": None,
        "category": "social_media",
    }
    seen_headers: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(req.headers))
        return httpx.Response(200)

    async with _mock_http(handler) as http:
        await _check_platform(
            platform,
            username="anyone",
            full_name="",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert seen_headers["accept-language"] == "en-US,en;q=0.9"
    assert seen_headers["x-custom"] == "1"
    assert "user-agent" in seen_headers


async def test_waf_response_returns_no_finding(sema):
    platform = {
        "name": "PyPI",
        "url_template": "https://example.com/{username}/",
        "probe_template": "https://example.com/{username}/",
        "method": "GET",
        "headers": {},
        "error_types": ["status_code"],
        "error_messages": [],
        "error_codes": [],
        "error_url_template": "",
        "regex_check": None,
        "category": "development",
    }

    def handler(_req: httpx.Request) -> httpx.Response:
        # Inject a known WAF fingerprint into an otherwise 200 response.
        body = f"<html><span id=\"challenge-error-text\">blocked</span></html>"
        return httpx.Response(200, text=body)

    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="anyone",
            full_name="",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert result is None  # WAF-blocked → inconclusive, don't emit


async def test_generic_landing_page_dropped_by_floor(sema):
    """A site that returns a generic landing page ('TikTok - Make Your Day')
    triggers the name-mismatch haircut and falls below the 0.25 confidence
    floor → no finding emitted."""
    platform = {
        "name": "TikTok",
        "url_template": "https://example.com/@{username}",
        "probe_template": "https://example.com/@{username}",
        "method": "GET",
        "headers": {},
        "error_types": ["status_code"],
        "error_messages": [],
        "error_codes": [],
        "error_url_template": "",
        "regex_check": None,
        "category": "social_media",
    }

    def handler(_req: httpx.Request) -> httpx.Response:
        body = "<html><head><title>TikTok - Make Your Day</title></head></html>"
        return httpx.Response(200, text=body)

    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="caitwdc",
            full_name="Caitlyn Walker",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert result is None


async def test_username_in_title_promotes_to_high_confidence(sema):
    """When the page title contains the username (e.g. 'caitwdc - Twitch'),
    it's a real user page — confidence must clear LOW."""
    platform = {
        "name": "Twitch",
        "url_template": "https://example.com/{username}",
        "probe_template": "https://example.com/{username}",
        "method": "GET",
        "headers": {},
        "error_types": ["status_code"],
        "error_messages": [],
        "error_codes": [],
        "error_url_template": "",
        "regex_check": None,
        "category": "social_media",
    }

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><head><title>caitwdc - Twitch</title></head></html>",
        )

    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="caitwdc",
            full_name="Caitlyn Walker",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert result is not None
    # username_in_title doubles the bare-URL signal → 0.50 confidence.
    assert result.confidence >= 0.50


async def test_user_not_found_in_title_vetoes_finding(sema):
    """Sherlock might say CLAIMED (status_code 200) for an SPA shell, but
    the rendered title literally says 'User not found'. We override and
    drop the finding."""
    platform = {
        "name": "Hashnode",
        "url_template": "https://example.com/@{username}",
        "probe_template": "https://example.com/@{username}",
        "method": "GET",
        "headers": {},
        "error_types": ["status_code"],
        "error_messages": [],
        "error_codes": [],
        "error_url_template": "",
        "regex_check": None,
        "category": "development",
    }

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><head><title>User not found | Hashnode</title></head></html>",
        )

    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="ghost",
            full_name="",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert result is None


# --------------------------- loader integration ------------------------------- #


def test_loader_returns_normalized_platforms():
    platforms = load_platforms()
    assert len(platforms) > 400  # we vendored ~478 sites
    for p in platforms[:50]:
        assert isinstance(p["name"], str) and p["name"]
        # The display URL always carries the username placeholder.
        assert "{username}" in p["url_template"]
        # POST sites (Anilist / Discord / Holopin) carry the username in
        # request_payload instead — probe_template can be a fixed endpoint.
        if not p.get("request_payload"):
            assert "{username}" in p["probe_template"]
        assert isinstance(p["error_types"], list) and p["error_types"]
        assert all(
            et in {"message", "status_code", "response_url"} for et in p["error_types"]
        )


def test_loader_anilist_post_payload_normalized():
    platforms = {p["name"]: p for p in load_platforms()}
    if "Anilist" not in platforms:
        pytest.skip("Anilist not present in vendored data")
    a = platforms["Anilist"]
    assert a["method"] == "POST"
    assert a["request_payload"]
    # The probe is the GraphQL endpoint; the display URL is the user page.
    assert "graphql" in a["probe_template"]
    assert "{username}" in a["url_template"]


async def test_post_site_substitutes_username_into_payload(sema):
    """Anilist-style: POST a JSON body with {} placeholder for the username."""
    platform = {
        "name": "Anilist",
        "url_template": "https://anilist.co/user/{username}/",
        "probe_template": "https://graphql.example.com/",
        "method": "POST",
        "headers": {},
        "request_payload": {
            "query": "query($name:String){User(name:$name){id}}",
            "variables": {"name": "{}"},
        },
        "error_types": ["status_code"],
        "error_messages": [],
        "error_codes": [],
        "error_url_template": "",
        "regex_check": None,
        "category": "social_media",
    }
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json as _json

        seen["method"] = req.method
        seen["body"] = _json.loads(req.content.decode("utf-8"))
        return httpx.Response(200, text="{}")

    async with _mock_http(handler) as http:
        result = await _check_platform(
            platform,
            username="josh",
            full_name="",
            ig_bio="",
            ig_phash=None,
            http=http,
            image_downloader=None,
            sentence_model=None,
            semaphore=sema,
        )
    assert result is not None
    assert seen["method"] == "POST"
    assert seen["body"]["variables"]["name"] == "josh"


def test_render_url_substitutes_username():
    assert (
        render_url("https://example.com/{username}", "alice")
        == "https://example.com/alice"
    )
    assert (
        render_url("https://example.com/{username}/about", "bob.smith")
        == "https://example.com/bob.smith/about"
    )
