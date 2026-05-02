"""Identity cross-reference pipeline.

For every platform in ``data/sherlock_data.json`` we issue an HTTP request
and apply Sherlock's matching semantics to decide if ``ig_username`` is
claimed on that site:

    * ``status_code`` — site exists if the response code is NOT in the
      configured ``errorCode`` list (default: anything outside 2xx is a
      "doesn't exist").
    * ``message`` — site exists if NONE of the configured error strings
      appear in the response body.
    * ``response_url`` — redirects are disabled; a 2xx means the user
      exists, anything else (typically a 30x) means a redirect to an
      error landing page.

When multiple ``errorType`` values are listed, ANY one of them saying
"user doesn't exist" wins — matching Sherlock upstream.

Confirmed accounts get scored against the Instagram baseline (display
name fuzzy match, profile-pic perceptual hash, bio embedding cosine).
A clearly-non-matching display name applies a confidence penalty so
generic landing pages don't get reported as "MEDIUM" hits.

Findings stream out one at a time so the dashboard renders live.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.data.loader import HIGH_RISK_PLATFORMS, load_platforms, render_url
from app.schemas.events import (
    finding as _finding_event,
)
from app.schemas.events import (
    pipeline_status as _pipeline_status_event,
)
from app.schemas.findings import Finding
from app.session_store import SessionState

logger = logging.getLogger(__name__)

PIPELINE_NAME = "identity"

_CONCURRENCY = 60
_REQUEST_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
_BODY_SCAN_BYTES = 4000
# Cap how much of the body we feed into the message-detector. Sherlock
# scans the entire body but most matches are within the first few KB and
# scanning megabytes of HTML hurts throughput.
_BODY_MATCH_BYTES = 200_000

# Bumped to HIGH if matched on a high-blast-radius platform.
_HIGH_RISK_PLATFORMS = HIGH_RISK_PLATFORMS

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,image/avif,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Direct copy of Sherlock's WAF challenge fingerprints (sherlock.py).
_WAF_FINGERPRINTS: tuple[str, ...] = (
    ".loading-spinner{visibility:hidden}body.no-js .challenge-running",
    '<span id="challenge-error-text">',
    "AwsWafIntegration.forceRefreshToken",
    "perimeterxIdentifiers",
    # Extras observed in the wild that produce 200/OK pages we should
    # treat as inconclusive rather than as a confirmed account hit.
    "Attention Required! | Cloudflare",
    "Please wait for verification",
    "Security Verification",
    "Making sure you&#39;re not a bot",
    "Making sure you're not a bot",
    "Checking your browser",
    "ERROR: The request could not be satisfied",
    "Client Challenge",
    "Just a moment&hellip;",
    "Just a moment...",
    "cf-browser-verification",
    "captcha-bypass",
    # 2024-2026 additions — see IDENTITY_FALSE_POSITIVES.md
    "challenges.cloudflare.com/turnstile",
    "/cdn-cgi/challenge-platform/",
    "cf-mitigated",
    "awswaf.com",
    "awswaf-token",
    "_pxhd",
    "px-captcha",
    'data-pxht="captcha"',
    "geo.captcha-delivery.com",
    "x-dd-b",
    "_abck",
    "ak_bmsc",
    "g-recaptcha",
)

# Page-level error/landing fingerprints. A site can return 200 (so
# Sherlock's status_code detector says "claimed") while the page is
# actually a "user not found" view — common for Next.js / Nuxt SPAs and
# CDN error overlays. If the title/first 1KB matches any of these we
# DROP the finding, overriding Sherlock's verdict.
_NEGATIVE_TITLE_PATTERNS: tuple[str, ...] = (
    "user not found",
    "profile not found",
    "page not found",
    "page no longer exists",
    "404 - page not found",
    "404 not found",
    "404: ",
    "página não existe",  # mercadolivre
    "this page doesn't exist",
    "this page does not exist",
    "не существует",  # ru "doesn't exist"
    "не найдена",  # ru "not found"
    "ошибка",  # ru "error"
    "page is unavailable",
    "border patrol",  # NationStates negative landing
    "log in to see",
    "sign up to see",
    "redirecting...",
)
_NEGATIVE_BODY_PATTERNS: tuple[str, ...] = (
    "user not found",
    "profile not found",
    "page no longer exists",
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_DESC_RE = re.compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_TW_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']twitter:description["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_TW_IMAGE_RE = re.compile(
    r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# Many real profile pages don't set og:image to the user's avatar (a site
# default ships there instead). apple-touch-icon is sometimes per-user;
# avatar-class img tags are a richer fallback for the rendered avatar.
_APPLE_TOUCH_RE = re.compile(
    r'<link[^>]+rel=["\']apple-touch-icon[^"\']*["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_AVATAR_IMG_RE = re.compile(
    r'<img[^>]+(?:class|id)=["\'][^"\']*avatar[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_DESCRIPTION_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

# `<a href="...">` extractor for the external_url crawl. Looks deliberately
# lax — linktree / beacons / personal sites use very different markup and
# we just want hosts, not parsed link metadata.
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def _normalize_host(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


async def _collect_external_url_hosts(
    external_url: str, http: httpx.AsyncClient
) -> frozenset[str]:
    """Fetch the IG bio's external_url (linktree, personal site, etc.) and
    return the set of distinct hosts it links to. Treated downstream as
    the user themselves vouching for those profiles — the strongest
    corroborating signal we can get cheaply."""
    if not external_url:
        return frozenset()
    try:
        resp = await http.get(
            external_url,
            headers=_HEADERS,
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
        )
    except Exception as exc:
        logger.debug("external_url crawl failed for %s: %s", external_url, exc)
        return frozenset()
    body = ""
    try:
        body = resp.text or ""
    except Exception:
        return frozenset()
    body = body[:_BODY_MATCH_BYTES]
    self_host = _normalize_host(external_url)
    hosts: set[str] = set()
    for match in _HREF_RE.finditer(body):
        href = match.group(1).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(external_url, href)
        host = _normalize_host(absolute)
        if not host or host == self_host:
            continue
        hosts.add(host)
    return frozenset(hosts)


# --------------------------------------------------------------------- #
# Lazy heavy-dep loaders                                                #
# --------------------------------------------------------------------- #

_SENTENCE_MODEL: Any | None = None
_SENTENCE_MODEL_LOCK = asyncio.Lock()
_SENTENCE_MODEL_BROKEN = False


def _load_sentence_model_sync() -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("all-MiniLM-L6-v2")


async def _get_sentence_model() -> Any | None:
    global _SENTENCE_MODEL, _SENTENCE_MODEL_BROKEN
    if _SENTENCE_MODEL is not None or _SENTENCE_MODEL_BROKEN:
        return _SENTENCE_MODEL
    async with _SENTENCE_MODEL_LOCK:
        if _SENTENCE_MODEL is not None or _SENTENCE_MODEL_BROKEN:
            return _SENTENCE_MODEL
        try:
            _SENTENCE_MODEL = await asyncio.to_thread(_load_sentence_model_sync)
        except Exception as exc:
            logger.warning("Falling back: sentence-transformers unavailable: %s", exc)
            _SENTENCE_MODEL_BROKEN = True
    return _SENTENCE_MODEL


def _phash_sync(image_bytes: bytes) -> Any | None:
    try:
        from PIL import Image
        import imagehash
    except Exception as exc:
        logger.debug("Pillow/imagehash not available: %s", exc)
        return None
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            return imagehash.phash(img)
    except Exception as exc:
        logger.debug("phash failed: %s", exc)
        return None


def _cosine_sync(model: Any, text_a: str, text_b: str) -> float:
    try:
        import numpy as np

        embeddings = model.encode(
            [text_a, text_b], normalize_embeddings=True, show_progress_bar=False
        )
        return float(np.dot(embeddings[0], embeddings[1]))
    except Exception as exc:
        logger.debug("cosine encode failed: %s", exc)
        return 0.0


def _token_sort_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    try:
        from rapidfuzz import fuzz

        return float(fuzz.token_sort_ratio(a, b)) / 100.0
    except Exception as exc:
        logger.debug("rapidfuzz unavailable: %s", exc)
        ta = set(a.lower().split())
        tb = set(b.lower().split())
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / max(len(ta | tb), 1)


# --------------------------------------------------------------------- #
# HTML parsing helpers                                                  #
# --------------------------------------------------------------------- #


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    if not m:
        return None
    raw = (m.group(1) or "").strip()
    return raw or None


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", _TAG_STRIP_RE.sub(" ", text)).strip()


def _extract_display_name(snippet: str) -> str | None:
    for pat in (_OG_TITLE_RE, _TITLE_RE):
        v = _first_match(pat, snippet)
        if v:
            return _strip_html(v)
    return None


def _extract_bio(snippet: str) -> str | None:
    for pat in (_OG_DESC_RE, _TW_DESC_RE, _DESCRIPTION_RE):
        v = _first_match(pat, snippet)
        if v:
            return _strip_html(v)
    return None


def _extract_image(snippet: str, base_url: str) -> str | None:
    for pat in (_OG_IMAGE_RE, _TW_IMAGE_RE, _AVATAR_IMG_RE, _APPLE_TOUCH_RE):
        v = _first_match(pat, snippet)
        if v:
            return urljoin(base_url, _strip_html(v))
    return None


def _hamming_score(a: Any | None, b: Any | None) -> float | None:
    if a is None or b is None:
        return None
    try:
        distance = int(a - b)
    except Exception:
        return None
    # Tighter cutoff than before: distance 8 ≈ similarity 0.875, which is
    # the floor where a pHash match is worth treating as positive
    # corroboration vs. coincidental visual overlap.
    if distance > 8:
        return None
    return max(0.0, 1.0 - distance / 64.0)


def _risk_for(confidence: float, platform_name: str) -> str:
    if confidence >= 0.8:
        risk = "HIGH"
    elif confidence >= 0.55:
        risk = "MEDIUM"
    else:
        risk = "LOW"
    if platform_name in _HIGH_RISK_PLATFORMS and confidence >= 0.5:
        risk = "HIGH"
    return risk


def _platform_remediation(name: str, url: str, category: str) -> str:
    if category == "professional":
        return (
            f"Review and lock down the public profile at {url}. "
            f"If it is not yours, report the impersonating account to {name}."
        )
    if category in {"commerce", "messaging"}:
        return (
            f"If this {name} account at {url} is yours, set it to private or "
            "delete it. If it is not yours, report it for impersonation."
        )
    return (
        f"Delete or rename the {name} account at {url}, "
        "or set the profile to private if you intend to keep it."
    )


# --------------------------------------------------------------------- #
# Sherlock-compatible matching                                          #
# --------------------------------------------------------------------- #


def _looks_like_waf(body: str) -> bool:
    if not body:
        return False
    return any(sig in body for sig in _WAF_FINGERPRINTS)


def _looks_like_not_found(title: str, body_head: str) -> bool:
    """Override Sherlock's CLAIMED verdict when the page itself says the
    user/page doesn't exist. Catches SPA shells and CDN error overlays
    that Sherlock can't detect upstream."""
    if title:
        t = title.lower()
        for pattern in _NEGATIVE_TITLE_PATTERNS:
            if pattern in t:
                return True
    if body_head:
        b = body_head.lower()
        for pattern in _NEGATIVE_BODY_PATTERNS:
            if pattern in b:
                return True
    return False


def _substitute_payload(value: Any, username: str) -> Any:
    """Recursively substitute Sherlock's ``{}`` placeholder in a request
    payload (Anilist's GraphQL query, Discord's username probe, etc.)."""
    if isinstance(value, str):
        if "{}" in value:
            return value.replace("{}", username)
        if "{username}" in value:
            return value.replace("{username}", username)
        return value
    if isinstance(value, dict):
        return {k: _substitute_payload(v, username) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_payload(v, username) for v in value]
    return value


def _is_claimed(
    platform: dict[str, Any],
    status_code: int,
    body_for_match: str,
    username: str = "",
) -> bool:
    """Decide whether a probe response indicates the username is claimed.

    Two paths:

    **WMN two-sided** (when the loader supplies ``e_code``/``e_string``/
    ``m_code``/``m_string``):
        FOUND iff  e_code == status AND e_string IN body
                   AND NOT (m_code == status AND m_string IN body)
        Anything else → inconclusive, return False.

    **Sherlock 3-mode fallback** (current loader): apply
    status_code/message/response_url. The body-level "user not found"
    veto runs at the call-site (``_looks_like_not_found``) which catches
    the soft-200 SPA class that this matcher otherwise misses.

    See ``IDENTITY_FALSE_POSITIVES.md`` for full rationale.
    """
    # ---- WMN two-sided when loader exposes positive markers ----
    e_code = int(platform.get("e_code") or 0)
    e_string = platform.get("e_string") or ""
    m_code = int(platform.get("m_code") or 0)
    m_string = platform.get("m_string") or ""
    if e_code > 0 and e_string and m_code > 0 and m_string:
        e_match = (status_code == e_code) and (e_string in body_for_match)
        m_match = (status_code == m_code) and (m_string in body_for_match)
        return bool(e_match and not m_match)

    # ---- Sherlock 3-mode fallback (used only when WMN markers absent) ----
    error_types: list[str] = list(platform.get("error_types") or [])
    if not error_types:
        error_types = ["status_code"]
    message_only = (
        "message" in error_types and "status_code" not in error_types
    )

    # Defensive 2xx gate for message-only sites: a 30x/4xx/5xx response
    # never has a valid profile body regardless of which negative
    # patterns aren't in it. Without this, redirects-to-error masquerade
    # as hits (AniWorld 302, etc.).
    if message_only and not (200 <= status_code < 300):
        return False

    for et in error_types:
        if et == "message":
            messages: list[str] = platform.get("error_messages") or []
            if any(msg and msg in body_for_match for msg in messages):
                return False
        elif et == "status_code":
            error_codes: list[int] = platform.get("error_codes") or []
            if error_codes:
                if status_code in error_codes:
                    return False
            elif not (200 <= status_code < 300):
                # Bare status-code-only sites: only 2xx counts as found.
                # follow_redirects=False at the call site means a 30x to
                # a login wall no longer masks "not found".
                return False
        elif et == "response_url":
            if not (200 <= status_code < 300):
                return False
        else:
            logger.debug(
                "Unknown errorType %r on %s; skipping", et, platform.get("name")
            )
            return False

    # Message-only Sherlock entries are the high-FP class: a 200 with
    # error string absent is taken as "claimed", which breaks when the
    # site's error message has rotated or when it ships a generic
    # landing page. Require the username to actually appear in the body
    # so we have a positive signal, not just absence-of-negative.
    if message_only and username:
        if username.lower() not in body_for_match[:8000].lower():
            return False
    return True


# --------------------------------------------------------------------- #
# Per-platform check                                                    #
# --------------------------------------------------------------------- #


async def _check_platform(
    platform: dict[str, Any],
    username: str,
    full_name: str,
    ig_bio: str,
    ig_phash: Any | None,
    http: httpx.AsyncClient,
    image_downloader: Any,
    sentence_model: Any | None,
    semaphore: asyncio.Semaphore,
    external_url_hosts: frozenset[str] = frozenset(),
) -> Finding | None:
    name: str = platform["name"]

    # WMN-style ``protected`` flag (set when the catalog ships it) — these
    # sites are WAF/CAPTCHA-fronted and probing them produces high-FP
    # results we can't validate. Sherlock data doesn't carry the flag, so
    # this is a no-op there; runtime WAF detection still catches them.
    if platform.get("protected"):
        return None

    # Per-site username regex — skip without ever issuing a request.
    regex_check = platform.get("regex_check")
    if regex_check:
        try:
            if not re.match(regex_check, username):
                return None
        except re.error:
            pass

    # Two-sided detection makes follow_redirects irrelevant for the body
    # match (we look for e_string explicitly), but a 30x to a login wall
    # or homepage still poisons the verdict. Disable redirects globally.
    follow_redirects = False

    method = (platform.get("method") or "GET").upper()
    probe_url = render_url(platform["probe_template"], username)
    display_url = render_url(platform["url_template"], username)

    # Lower-case keys so per-site overrides REPLACE the defaults instead
    # of producing duplicate headers (httpx would join them with ', ').
    request_headers = {k.lower(): v for k, v in _HEADERS.items()}
    extra_headers = platform.get("headers") or {}
    if isinstance(extra_headers, dict):
        for k, v in extra_headers.items():
            if v is None:
                continue
            request_headers[str(k).lower()] = str(v)

    request_kwargs: dict[str, Any] = {
        "headers": request_headers,
        "timeout": _REQUEST_TIMEOUT,
        "follow_redirects": follow_redirects,
    }
    payload = platform.get("request_payload")
    if isinstance(payload, dict) and method in {"POST", "PUT"}:
        request_kwargs["json"] = _substitute_payload(payload, username)

    async with semaphore:
        # One retry on transient transport errors. Jittered 1-3s backoff
        # so a flaky platform doesn't burn the whole pipeline on a 502
        # but we also don't synchronize a thundering herd on retry.
        resp = None
        for attempt in range(2):
            try:
                resp = await http.request(method, probe_url, **request_kwargs)
                break
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                if attempt == 0:
                    await asyncio.sleep(0.2 + random.random() * 0.3)
                    continue
                logger.debug("Skipping %s (%s): %s", name, probe_url, exc)
                return None
            except Exception as exc:
                logger.debug("Unexpected error checking %s: %s", name, exc)
                return None
        if resp is None:
            return None

        try:
            body_full = resp.text or ""
        except Exception:
            body_full = ""

    body_for_match = body_full[:_BODY_MATCH_BYTES]

    # WAF / anti-bot challenge → inconclusive, don't emit.
    if _looks_like_waf(body_for_match):
        return None

    if not _is_claimed(platform, resp.status_code, body_for_match, username):
        return None

    # ---------- scoring ----------
    snippet = body_full[:_BODY_SCAN_BYTES]
    display_name = _extract_display_name(snippet) or ""
    bio_text = _extract_bio(snippet) or ""

    # Veto: page itself says "user not found" / "page not found" /
    # captcha / etc. — drop the finding even if Sherlock said CLAIMED.
    if _looks_like_not_found(display_name, snippet):
        return None

    name_similarity = (
        _token_sort_ratio(full_name, display_name)
        if full_name and display_name
        else 0.0
    )

    # Username in the page title is a strong positive signal even when
    # the rendered display_name is the bare site name (e.g. "caitwdc -
    # Twitch", "caitwdc | InternetArchive"). Sherlock's status_code can
    # match generic landing pages that DON'T contain the username — this
    # promotes the real user pages ahead of those.
    username_in_title = bool(
        username and display_name and username.lower() in display_name.lower()
    )

    photo_similarity: float | None = None
    candidate_image_url = _extract_image(snippet, display_url)
    if candidate_image_url and ig_phash is not None and image_downloader is not None:
        try:
            img_bytes, _ = await image_downloader.download(candidate_image_url)
            candidate_phash = await asyncio.to_thread(_phash_sync, img_bytes)
            photo_similarity = _hamming_score(ig_phash, candidate_phash)
        except Exception as exc:
            logger.debug("photo compare failed for %s: %s", name, exc)
            photo_similarity = None

    bio_similarity: float | None = None
    if (
        sentence_model is not None
        and ig_bio
        and bio_text
        and len(ig_bio) > 20
        and len(bio_text) > 20
    ):
        try:
            bio_similarity = await asyncio.to_thread(
                _cosine_sync, sentence_model, ig_bio, bio_text
            )
        except Exception as exc:
            logger.debug("bio compare failed for %s: %s", name, exc)
            bio_similarity = None

    # External-URL corroboration: the user's IG bio links out to a personal
    # site / linktree, and that site links to THIS profile's host. As close
    # to a self-attestation as we can get from public signals — strongest
    # weight in the mix.
    display_host = _normalize_host(display_url)
    external_url_match = bool(
        display_host and external_url_hosts and display_host in external_url_hosts
    )

    # Weighted-mean confidence. Bare URL hit (Sherlock-confirmed) is
    # 0.30; a strong corroborating signal can push it higher.
    weights: list[tuple[float, float]] = [(0.30, 1.0)]
    if external_url_match:
        weights.append((0.40, 1.0))
    if username_in_title:
        # Direct corroboration — the username appears on the rendered
        # page title — typical of true user-profile pages.
        weights.append((0.30, 1.0))
    if display_name and name_similarity >= 0.4:
        weights.append((0.35, max(0.0, min(1.0, name_similarity))))
    if photo_similarity is not None:
        # Stronger weight than before: with the tighter distance cutoff in
        # _hamming_score, a non-None similarity now means "near-identical
        # avatar" rather than "loosely similar".
        weights.append((0.30, max(0.0, min(1.0, photo_similarity))))
    if bio_similarity is not None:
        weights.append((0.15, max(0.0, min(1.0, bio_similarity))))

    if len(weights) == 1:
        confidence = 0.30
    else:
        weight_sum = sum(w for w, _ in weights)
        score_sum = sum(w * s for w, s in weights)
        confidence = score_sum / weight_sum if weight_sum else 0.0

    # Penalty: page rendered a clearly non-matching display name without
    # the username being anywhere in the title. This is what a generic
    # site landing page looks like ("TikTok - Make Your Day"). 30% haircut.
    if (
        display_name
        and full_name
        and name_similarity < 0.15
        and not username_in_title
    ):
        confidence *= 0.7

    confidence = round(max(0.0, min(1.0, confidence)), 3)

    # Floor: anything that only made it through with the haircut applied
    # is almost certainly an SPA shell / generic landing page that
    # Sherlock's status_code or message detector can't tell apart from
    # a real user. Bare URL hits (0.30) and any corroborated hit stay.
    if confidence < 0.25:
        return None

    risk = _risk_for(confidence, name)

    evidence: list[str] = [
        f"Username '{username}' resolves to a live page at {display_url}"
    ]
    if external_url_match:
        evidence.append(
            f"Linked from the user's bio external_url (host {display_host})"
        )
    if display_name:
        evidence.append(
            f"Page display name '{display_name[:120]}' "
            f"(name similarity {name_similarity:.2f})"
        )
    if photo_similarity is not None:
        evidence.append(
            f"Profile picture pHash matches Instagram avatar "
            f"(similarity {photo_similarity:.2f})"
        )
    if bio_similarity is not None:
        evidence.append(
            f"Bio text overlaps Instagram bio (cosine {bio_similarity:.2f})"
        )

    metadata = {
        "platform": name,
        "category": platform.get("category", "social_media"),
        "url": display_url,
        "host": urlparse(display_url).netloc,
        "name_similarity": round(name_similarity, 3) if display_name else None,
        "photo_similarity": (
            round(photo_similarity, 3) if photo_similarity is not None else None
        ),
        "bio_similarity": (
            round(bio_similarity, 3) if bio_similarity is not None else None
        ),
        "display_name": display_name or None,
        "detection": "+".join(platform.get("error_types") or []) or "wmn_two_sided",
        "nsfw": bool(platform.get("nsfw", False)),
        "external_url_match": external_url_match,
    }

    remediation = _platform_remediation(
        name, display_url, platform.get("category", "social_media")
    )

    return Finding(
        source=f"identity:{name}",
        evidence_chain=evidence,
        confidence=confidence,
        risk_level=risk,
        remediation=remediation,
        metadata=metadata,
    )


# --------------------------------------------------------------------- #
# Pipeline entry point                                                  #
# --------------------------------------------------------------------- #


async def run(session: SessionState) -> None:
    outcomes: dict[str, str] = session.data.setdefault("pipeline_outcomes", {})
    findings_log: list[dict] = session.data.setdefault("findings", [])

    try:
        await session.publish_event(
            _pipeline_status_event(PIPELINE_NAME, "running", "scanning platforms")
        )

        profile: dict | None = session.data.get("profile")
        if not profile or not profile.get("username"):
            outcomes[PIPELINE_NAME] = "error"
            await session.publish_event(
                _pipeline_status_event(
                    PIPELINE_NAME, "error", "missing Instagram profile in session"
                )
            )
            return

        username = str(profile.get("username", "")).strip()
        full_name = str(profile.get("full_name", "")).strip()
        ig_bio = str(profile.get("biography", "")).strip()
        profile_pic_url = str(profile.get("profile_pic_url", "")).strip()
        external_url = str(profile.get("external_url") or "").strip()

        http: httpx.AsyncClient | None = session.data.get("http")
        if http is None:
            http = httpx.AsyncClient(headers=_HEADERS, follow_redirects=True)
            session.data["http"] = http

        # Crawl the bio external_url once up front so every per-platform
        # check can corroborate against the hosts it links to.
        external_url_hosts: frozenset[str] = frozenset()
        if external_url:
            try:
                external_url_hosts = await _collect_external_url_hosts(
                    external_url, http
                )
            except Exception as exc:
                logger.debug("external_url collection failed: %s", exc)

        image_downloader = session.data.get("image_downloader")

        ig_phash: Any | None = None
        if profile_pic_url and image_downloader is not None:
            try:
                ig_bytes, _ = await image_downloader.download(profile_pic_url)
                ig_phash = await asyncio.to_thread(_phash_sync, ig_bytes)
            except Exception as exc:
                logger.debug("Could not pHash IG avatar: %s", exc)
                ig_phash = None

        sentence_model = await _get_sentence_model()

        platforms = await asyncio.to_thread(load_platforms)
        semaphore = asyncio.Semaphore(_CONCURRENCY)

        async def _runner(p: dict[str, Any]) -> Finding | None:
            try:
                return await _check_platform(
                    p,
                    username=username,
                    full_name=full_name,
                    ig_bio=ig_bio,
                    ig_phash=ig_phash,
                    http=http,
                    image_downloader=image_downloader,
                    sentence_model=sentence_model,
                    semaphore=semaphore,
                    external_url_hosts=external_url_hosts,
                )
            except Exception as exc:
                logger.debug("platform %s blew up: %s", p.get("name"), exc)
                return None

        tasks = [asyncio.create_task(_runner(p)) for p in platforms]

        emitted = 0
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result is None:
                continue
            d = result.to_dict()
            d["_pipeline"] = PIPELINE_NAME
            findings_log.append(d)
            await session.publish_event(_finding_event(PIPELINE_NAME, d))
            emitted += 1

        outcomes[PIPELINE_NAME] = "complete"
        await session.publish_event(
            _pipeline_status_event(
                PIPELINE_NAME,
                "complete",
                f"{emitted} match(es) across {len(platforms)} platforms",
            )
        )
    except asyncio.CancelledError:
        outcomes[PIPELINE_NAME] = "error"
        raise
    except Exception as exc:
        logger.exception("identity pipeline crashed")
        outcomes[PIPELINE_NAME] = "error"
        try:
            await session.publish_event(
                _pipeline_status_event(PIPELINE_NAME, "error", f"{exc}")
            )
        except Exception:
            pass
