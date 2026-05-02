"""Wayback Machine snapshot lookups.

Free, unauthenticated wrapper around the Internet Archive Availability
API and the snapshot HTML endpoint. Useful for two reasons:

1. LinkedIn / Facebook / X gate logged-out HTML behind interstitials,
   but the archived versions render the full profile body. Pulling a
   recent snapshot of a LinkedIn URL surfaced via dorks can expose
   bio, employer, school, and contact info that the live URL hides.
2. Snapshots from before a takedown / cleanup request still leak the
   info the user thought they removed.

We use:
- ``http://archive.org/wayback/available?url={url}`` for the most
  recent snapshot pointer
- ``http://web.archive.org/web/{ts}id_/{url}`` (the ``id_`` flag gives
  us the original raw HTML without the Wayback toolbar overlay)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


_AVAILABILITY_URL = "https://archive.org/wayback/available"
_USER_AGENT = "NODOXX-self-audit/0.1"
_TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True)
class WaybackSnapshot:
    original_url: str
    snapshot_url: str
    timestamp: str            # YYYYMMDDHHMMSS

    @property
    def raw_url(self) -> str:
        """Snapshot URL with the ``id_`` flag set so the response body
        is the original archived HTML (no IA toolbar injection)."""
        if not self.snapshot_url or not self.timestamp:
            return self.snapshot_url
        # Snapshot URLs come back as
        #   https://web.archive.org/web/{ts}/{url}
        # Splice ``id_`` after the timestamp.
        marker = f"/web/{self.timestamp}/"
        if marker in self.snapshot_url:
            return self.snapshot_url.replace(
                marker, f"/web/{self.timestamp}id_/", 1
            )
        return self.snapshot_url


async def latest_snapshot(
    url: str, http: httpx.AsyncClient
) -> WaybackSnapshot | None:
    """Return the most recent Wayback snapshot for ``url`` or ``None``
    if no snapshot exists (or the API errors)."""
    if not url:
        return None
    try:
        resp = await http.get(
            _AVAILABILITY_URL,
            params={"url": url},
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT_SECONDS,
        )
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        logger.debug("Wayback availability failed for %s: %s", url, exc)
        return None

    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except Exception:
        return None

    closest = (
        payload.get("archived_snapshots", {}).get("closest", {})
        if isinstance(payload, dict)
        else {}
    )
    if not isinstance(closest, dict) or not closest.get("available"):
        return None

    snapshot_url = str(closest.get("url") or "").strip()
    timestamp = str(closest.get("timestamp") or "").strip()
    if not snapshot_url or not timestamp:
        return None

    return WaybackSnapshot(
        original_url=url,
        snapshot_url=snapshot_url,
        timestamp=timestamp,
    )


async def fetch_snapshot_text(
    snapshot: WaybackSnapshot,
    http: httpx.AsyncClient,
    max_chars: int = 8000,
) -> str:
    """Fetch the archived HTML body and return up to ``max_chars`` of
    plain-text content. Light-weight tag stripping — for richer
    extraction the caller can pass the raw HTML through
    ``trafilatura_fetch`` instead.
    """
    target = snapshot.raw_url
    if not target:
        return ""
    try:
        resp = await http.get(
            target,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"},
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        logger.debug("Wayback snapshot fetch failed: %s", exc)
        return ""

    if resp.status_code != 200:
        return ""
    body = resp.text or ""
    if not body:
        return ""

    # Cheap tag strip — we don't need DOM fidelity here; only string
    # extraction for downstream regex / LLM scans.
    import re as _re

    text = _re.sub(r"<script[\s\S]*?</script>", " ", body, flags=_re.I)
    text = _re.sub(r"<style[\s\S]*?</style>", " ", text, flags=_re.I)
    text = _re.sub(r"<[^>]+>", " ", text)
    text = _re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]
