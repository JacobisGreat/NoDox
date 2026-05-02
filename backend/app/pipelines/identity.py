"""Identity cross-reference pipeline.

For every platform in ``data/platforms.json``:
    * issue an HTTP GET (or HEAD) at ``url_template.format(username=ig_username)``
    * if the response status is in ``valid_status`` AND none of the
      ``error_indicators`` are visible in the body's first 2 kB, treat the
      account as a possible match
    * for each candidate, score the match with a weighted blend of:
        - username equality (normalized, given)
        - display-name fuzzy similarity (rapidfuzz)
        - profile-pic perceptual hash similarity (imagehash)
        - bio cosine similarity (sentence-transformers, lazy load)
    * emit a ``finding`` event immediately (never buffer)

The whole thing runs at ``Semaphore(30)`` concurrency. We aggressively
swallow per-platform exceptions; one site flaking should not stop the
audit.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.data.loader import load_platforms, render_url
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

_CONCURRENCY = 30
_REQUEST_TIMEOUT = httpx.Timeout(5.0, connect=5.0)
_BODY_SCAN_BYTES = 2000

# Bumped to HIGH if matched on a high-blast-radius platform.
_HIGH_RISK_PLATFORMS = {"LinkedIn", "GitHub", "Reddit", "Facebook"}

_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,image/avif,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

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
_DESCRIPTION_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


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
        # Fallback: simple ratio of common-token length.
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
    for pat in (_OG_IMAGE_RE, _TW_IMAGE_RE):
        v = _first_match(pat, snippet)
        if v:
            return urljoin(base_url, _strip_html(v))
    return None


def _has_error_indicator(snippet: str, indicators: list[str]) -> bool:
    if not indicators:
        return False
    lowered = snippet.lower()
    for needle in indicators:
        if needle and needle.lower() in lowered:
            return True
    return False


def _hamming_score(a: Any | None, b: Any | None) -> float | None:
    if a is None or b is None:
        return None
    try:
        distance = int(a - b)
    except Exception:
        return None
    if distance > 10:
        return None
    return max(0.0, 1.0 - distance / 64.0)


def _risk_for(confidence: float, platform_name: str) -> str:
    if confidence >= 0.8:
        risk = "HIGH"
    elif confidence >= 0.5:
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
            "If it is not yours, report the impersonating account to {name}."
        ).format(name=name)
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
) -> Finding | None:
    name = platform["name"]
    url = render_url(platform["url_template"], username)
    method = platform.get("method", "GET").upper()
    valid_status = platform.get("valid_status", [200])
    indicators = platform.get("error_indicators", [])

    async with semaphore:
        try:
            resp = await http.request(
                method,
                url,
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.debug("Skipping %s (%s): %s", name, url, exc)
            return None
        except Exception as exc:
            logger.debug("Unexpected error checking %s: %s", name, exc)
            return None

        if resp.status_code not in valid_status:
            return None

        try:
            body = resp.text or ""
        except Exception:
            body = ""
        snippet = body[:_BODY_SCAN_BYTES]
        if _has_error_indicator(snippet, indicators):
            return None

    # ---------- scoring ----------
    display_name = _extract_display_name(snippet) or ""
    bio_text = _extract_bio(snippet) or ""

    name_similarity = (
        _token_sort_ratio(full_name, display_name) if full_name and display_name else 0.0
    )

    photo_similarity: float | None = None
    candidate_image_url = _extract_image(snippet, url)
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

    weights: list[tuple[float, float]] = []
    weights.append((0.30, 1.0))  # username matched (we issued the URL)
    if display_name:
        weights.append((0.30, max(0.0, min(1.0, name_similarity))))
    if photo_similarity is not None:
        weights.append((0.25, max(0.0, min(1.0, photo_similarity))))
    if bio_similarity is not None:
        weights.append((0.15, max(0.0, min(1.0, bio_similarity))))

    if len(weights) == 1:
        confidence = 0.30
    else:
        weight_sum = sum(w for w, _ in weights)
        score_sum = sum(w * s for w, s in weights)
        confidence = score_sum / weight_sum if weight_sum else 0.0

    confidence = round(max(0.0, min(1.0, confidence)), 3)
    risk = _risk_for(confidence, name)

    evidence: list[str] = [f"Username '{username}' resolves to a live page at {url}"]
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
            f"Bio text overlaps Instagram bio "
            f"(cosine {bio_similarity:.2f})"
        )

    metadata = {
        "platform": name,
        "category": platform.get("category", "other"),
        "url": url,
        "host": urlparse(url).netloc,
        "name_similarity": round(name_similarity, 3) if display_name else None,
        "photo_similarity": (
            round(photo_similarity, 3) if photo_similarity is not None else None
        ),
        "bio_similarity": (
            round(bio_similarity, 3) if bio_similarity is not None else None
        ),
        "display_name": display_name or None,
    }

    remediation = _platform_remediation(name, url, platform.get("category", "other"))

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

        http: httpx.AsyncClient | None = session.data.get("http")
        if http is None:
            http = httpx.AsyncClient(headers=_HEADERS, follow_redirects=True)
            session.data["http"] = http

        image_downloader = session.data.get("image_downloader")

        # Pre-load reference profile picture (best effort).
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
                PIPELINE_NAME, "complete", f"{emitted} match(es) across {len(platforms)} platforms"
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
