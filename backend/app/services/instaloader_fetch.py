"""Anonymous Instagram fetcher.

Originally built on the ``instaloader`` library, but Instagram's public
``graphql/query`` endpoint now returns 403 to every unauthenticated
client, which causes ``instaloader.Profile.from_username`` to raise
``ProfileNotExistsException`` for *every* real account. We work around
that by hitting the still-anonymous mobile API endpoint
``i.instagram.com/api/v1/users/web_profile_info/?username=...`` ourselves
with the documented public ``X-IG-App-ID`` and parsing its JSON shape
into our existing ``InstagramProfile`` / ``InstagramPost`` dataclasses.

The module name and public surface (``fetch_profile_and_posts``,
``InstaProfileNotFound``, ``InstaProfilePrivate``, ``InstaRateLimited``,
``InstaFetchError``) are preserved so callers do not change.

Caveats:
    * The ``web_profile_info`` endpoint returns the most recent ~12
      timeline posts inline. Pagination requires GraphQL, which is the
      403'd path, so we cap effective ``max_posts`` at what the response
      ships even when the caller asks for more.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.schemas.profile import InstagramPost, InstagramProfile


class InstaProfileNotFound(Exception):
    pass


class InstaProfilePrivate(Exception):
    pass


class InstaRateLimited(Exception):
    pass


class InstaFetchError(Exception):
    pass


@dataclass
class FetchResult:
    profile: InstagramProfile
    posts: list[InstagramPost]


_WEB_PROFILE_INFO_URL = (
    "https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
)

# Public web client identifier — the same value the instagram.com web app
# sends. Required; without it the endpoint returns 401.
_IG_APP_ID = "936619743392459"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-IG-App-ID": _IG_APP_ID,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.instagram.com/",
}


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_caption(node: dict[str, Any]) -> str | None:
    edges = ((node.get("edge_media_to_caption") or {}).get("edges")) or []
    if not edges:
        return None
    text = ((edges[0] or {}).get("node") or {}).get("text")
    if isinstance(text, str) and text.strip():
        return text
    return None


def _location_fields(node: dict[str, Any]) -> tuple[str | None, int | None]:
    loc = node.get("location")
    if not isinstance(loc, dict):
        return None, None
    name = loc.get("name") if isinstance(loc.get("name"), str) else None
    raw_id = loc.get("id")
    location_id: int | None = None
    if raw_id is not None:
        try:
            location_id = int(raw_id)
        except (TypeError, ValueError):
            location_id = None
    return name, location_id


def _tagged_users(node: dict[str, Any]) -> list[str]:
    edges = ((node.get("edge_media_to_tagged_user") or {}).get("edges")) or []
    out: list[str] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        user = ((edge.get("node") or {}).get("user")) or {}
        uname = user.get("username")
        if isinstance(uname, str) and uname:
            out.append(uname)
    return out


def _post_from_node(node: dict[str, Any]) -> InstagramPost | None:
    if not isinstance(node, dict):
        return None
    shortcode = node.get("shortcode") or ""
    image_url = (
        node.get("display_url")
        or node.get("thumbnail_src")
        or ""
    )
    if not shortcode or not image_url:
        return None
    location_name, location_id = _location_fields(node)
    ts = node.get("taken_at_timestamp")
    taken_at_iso = ""
    if isinstance(ts, (int, float)):
        try:
            taken_at_iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            taken_at_iso = ""
    return InstagramPost(
        shortcode=str(shortcode),
        image_url=str(image_url),
        caption=_first_caption(node),
        location_name=location_name,
        location_id=location_id,
        taken_at_iso=taken_at_iso,
        is_video=bool(node.get("is_video")),
        tagged_users=_tagged_users(node),
    )


def _profile_from_user(user: dict[str, Any], requested: str) -> InstagramProfile:
    timeline = user.get("edge_owner_to_timeline_media") or {}
    return InstagramProfile(
        username=str(user.get("username") or requested),
        full_name=str(user.get("full_name") or ""),
        biography=str(user.get("biography") or ""),
        followers=_safe_int((user.get("edge_followed_by") or {}).get("count")),
        followees=_safe_int((user.get("edge_follow") or {}).get("count")),
        is_private=bool(user.get("is_private")),
        is_verified=bool(user.get("is_verified")),
        external_url=user.get("external_url") or None,
        profile_pic_url=str(
            user.get("profile_pic_url_hd") or user.get("profile_pic_url") or ""
        ),
        post_count_total=_safe_int(timeline.get("count")),
    )


def _fetch_sync(username: str, max_posts: int) -> FetchResult:
    url = _WEB_PROFILE_INFO_URL.format(username=username)
    try:
        with httpx.Client(
            headers=_DEFAULT_HEADERS,
            timeout=httpx.Timeout(15.0, connect=10.0),
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        raise InstaFetchError(f"network error: {exc}") from exc

    status = resp.status_code
    if status == 404:
        raise InstaProfileNotFound(f"Profile '{username}' does not exist")
    if status in (401, 403):
        raise InstaRateLimited(
            f"Instagram refused the anonymous lookup (HTTP {status}); "
            "the public endpoint may be temporarily walled."
        )
    if status == 429:
        raise InstaRateLimited("Instagram rate limit (HTTP 429)")
    if status >= 500:
        raise InstaRateLimited(f"Instagram upstream error (HTTP {status})")
    if status != 200:
        raise InstaFetchError(f"Unexpected HTTP {status} from Instagram")

    try:
        body = resp.json()
    except ValueError as exc:
        raise InstaFetchError(f"Instagram returned non-JSON body: {exc}") from exc

    user = (body or {}).get("data", {}).get("user")
    if not isinstance(user, dict):
        # Some cloaked / shadowed profiles come back 200 with user=null.
        raise InstaProfileNotFound(f"Profile '{username}' not visible")

    profile = _profile_from_user(user, requested=username)

    if profile.is_private:
        return FetchResult(profile=profile, posts=[])

    edges = ((user.get("edge_owner_to_timeline_media") or {}).get("edges")) or []
    posts: list[InstagramPost] = []
    for edge in edges:
        if len(posts) >= max_posts:
            break
        node = (edge or {}).get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict):
            continue
        post = _post_from_node(node)
        if post is not None:
            posts.append(post)

    return FetchResult(profile=profile, posts=posts)


async def fetch_profile_and_posts(
    username: str, max_posts: int = 50
) -> FetchResult:
    return await asyncio.to_thread(_fetch_sync, username, max_posts)
