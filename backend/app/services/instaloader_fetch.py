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
import os
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


def _session_cookies() -> dict[str, str] | None:
    sessionid = (os.getenv("IG_SESSIONID") or "").strip()
    if not sessionid:
        return None
    cookies = {"sessionid": sessionid}
    ds_user_id = (os.getenv("IG_DS_USER_ID") or "").strip()
    if ds_user_id:
        cookies["ds_user_id"] = ds_user_id
    csrftoken = (os.getenv("IG_CSRFTOKEN") or "").strip()
    if csrftoken:
        cookies["csrftoken"] = csrftoken
    return cookies


_USER_FEED_URL = "https://i.instagram.com/api/v1/feed/user/{user_id}/?count={count}"


def _post_from_feed_item(item: dict[str, Any]) -> InstagramPost | None:
    """Map a mobile-API feed item to InstagramPost.

    The mobile feed shape is different from the web GraphQL shape used
    inside ``web_profile_info``. Carousels (media_type=8) put the first
    image inside ``carousel_media[0]``; reels (media_type=2 with
    ``product_type='clips'``) still expose a poster frame in
    ``image_versions2``.
    """
    if not isinstance(item, dict):
        return None
    code = item.get("code") or ""
    if not code:
        return None
    media_type = item.get("media_type")
    is_video = media_type == 2

    def _first_image(node: dict[str, Any]) -> str:
        iv2 = node.get("image_versions2") or {}
        cands = iv2.get("candidates") or []
        if cands and isinstance(cands[0], dict):
            return str(cands[0].get("url") or "")
        return ""

    image_url = ""
    if media_type == 8:  # carousel
        carousel = item.get("carousel_media") or []
        if carousel:
            image_url = _first_image(carousel[0])
    if not image_url:
        image_url = _first_image(item)
    if not image_url:
        return None

    caption_obj = item.get("caption")
    caption = None
    if isinstance(caption_obj, dict):
        text = caption_obj.get("text")
        if isinstance(text, str) and text.strip():
            caption = text

    loc_obj = item.get("location")
    location_name: str | None = None
    location_id: int | None = None
    if isinstance(loc_obj, dict):
        if isinstance(loc_obj.get("name"), str):
            location_name = loc_obj["name"]
        raw_id = loc_obj.get("pk") or loc_obj.get("id")
        if raw_id is not None:
            try:
                location_id = int(raw_id)
            except (TypeError, ValueError):
                location_id = None

    tagged: list[str] = []
    usertags = item.get("usertags")
    if isinstance(usertags, dict):
        for entry in usertags.get("in") or []:
            user = (entry or {}).get("user") if isinstance(entry, dict) else None
            uname = (user or {}).get("username") if isinstance(user, dict) else None
            if isinstance(uname, str) and uname:
                tagged.append(uname)

    ts = item.get("taken_at")
    taken_at_iso = ""
    if isinstance(ts, (int, float)):
        try:
            taken_at_iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            taken_at_iso = ""

    return InstagramPost(
        shortcode=str(code),
        image_url=image_url,
        caption=caption,
        location_name=location_name,
        location_id=location_id,
        taken_at_iso=taken_at_iso,
        is_video=is_video,
        tagged_users=tagged,
    )


def _fetch_user_feed_sync(
    client: httpx.Client, user_id: str, max_posts: int
) -> list[InstagramPost]:
    """Fallback when web_profile_info returns 0 inline edges.

    Meta has progressively trimmed the inline timeline payload on
    ``web_profile_info``, so for cookie-authenticated callers we follow
    up with the mobile feed endpoint to actually see the user's posts.
    """
    url = _USER_FEED_URL.format(user_id=user_id, count=max(max_posts, 12))
    try:
        resp = client.get(url)
    except httpx.HTTPError:
        return []
    if resp.status_code != 200:
        return []
    try:
        body = resp.json()
    except ValueError:
        return []
    items = (body or {}).get("items") or []
    out: list[InstagramPost] = []
    for item in items:
        if len(out) >= max_posts:
            break
        post = _post_from_feed_item(item)
        if post is not None:
            out.append(post)
    return out


def _fetch_sync(username: str, max_posts: int) -> FetchResult:
    url = _WEB_PROFILE_INFO_URL.format(username=username)
    try:
        with httpx.Client(
            headers=_DEFAULT_HEADERS,
            cookies=_session_cookies(),
            timeout=httpx.Timeout(60.0, connect=30.0),
            follow_redirects=True,
        ) as client:
            resp = client.get(url)

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
                raise InstaFetchError(
                    f"Instagram returned non-JSON body: {exc}"
                ) from exc

            user = (body or {}).get("data", {}).get("user")
            if not isinstance(user, dict):
                raise InstaProfileNotFound(f"Profile '{username}' not visible")

            profile = _profile_from_user(user, requested=username)

            if profile.is_private:
                return FetchResult(profile=profile, posts=[])

            edges = (
                (user.get("edge_owner_to_timeline_media") or {}).get("edges")
            ) or []
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

            # Fallback: web_profile_info increasingly returns an empty edges
            # list even for public accounts with posts. When we have session
            # cookies and the profile claims posts, hit the mobile user feed
            # to actually retrieve them.
            if (
                not posts
                and profile.post_count_total > 0
                and _session_cookies() is not None
            ):
                user_id = user.get("id")
                if user_id:
                    posts = _fetch_user_feed_sync(client, str(user_id), max_posts)

            return FetchResult(profile=profile, posts=posts)
    except httpx.HTTPError as exc:
        raise InstaFetchError(f"network error: {exc}") from exc


async def fetch_profile_and_posts(
    username: str, max_posts: int = 50
) -> FetchResult:
    return await asyncio.to_thread(_fetch_sync, username, max_posts)
