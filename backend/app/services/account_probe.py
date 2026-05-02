"""Sherlock-lite account probe for the web_footprint pipeline.

Reuses the merged platform catalog from Workstream A
(``backend/app/data/platforms.json``), filtered to a high-signal subset
(professional, development, paste/credentials sites). Fires async GETs
and applies the same body-scan heuristics as ``pipelines/identity.py``
without the heavy ML scoring — a hit just means "this username exists
on a sensitive platform."

Findings emitted from this service are categorically distinct from the
identity pipeline: identity does deep cross-reference scoring against
the IG profile; this service is a fast surface scan at footprint time.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.data.loader import load_platforms, render_url

logger = logging.getLogger(__name__)


# Categories worth probing during the web-footprint pass. Social-media
# overlap with the identity pipeline is intentionally excluded.
_HIGH_SIGNAL_CATEGORIES = {"professional", "development", "messaging", "commerce"}

# Override: a curated list of sites that are *always* high-signal regardless
# of their listed category — paste sites, credentials dumps, etc.
_ALWAYS_INCLUDE = {
    "pastebin",
    "github",
    "gitlab",
    "linkedin",
    "haveibeenpwned",
    "leakcheck",
    "dehashed",
}

# Sherlock/WMN error markers can sit deep in HTML for Discourse / Vue / SPA
# pages whose 404 templates render after sizable nav/header markup
# (Signal's community forum is the canonical example — "Oops!" lives at
# byte ~11.5k). Scan the first 64 KB; substring search is cheap.
_BODY_SCAN_BYTES = 65536
_REQUEST_TIMEOUT = httpx.Timeout(8.0, connect=5.0)
_DEFAULT_CONCURRENCY = 8

_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True)
class ProbeHit:
    site: str
    url: str
    category: str
    risk_level: str  # "MEDIUM" | "HIGH"


def _select_platforms() -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for entry in load_platforms():
        cat = entry.get("category", "other")
        name_lower = entry.get("name", "").lower()
        if cat in _HIGH_SIGNAL_CATEGORIES or any(
            n in name_lower for n in _ALWAYS_INCLUDE
        ):
            selected.append(entry)
    return selected


def _is_high_risk(name: str, category: str) -> bool:
    name_lower = name.lower()
    if any(n in name_lower for n in ("paste", "github", "gitlab", "linkedin")):
        return True
    return category == "professional"


async def _check_one(
    platform: dict[str, Any],
    username: str,
    http: httpx.AsyncClient,
    sem: asyncio.Semaphore,
) -> ProbeHit | None:
    """WhatsMyName two-sided account probe.

    Skips POST sites and WAF/CAPTCHA-fronted sites. A "found" verdict
    requires both the e_code/e_string positive marker AND the absence of
    the m_string negative marker. See ``IDENTITY_FALSE_POSITIVES.md``.
    """
    name = platform["name"]
    method = platform.get("method", "GET").upper()
    if method != "GET":
        return None  # POST sites handled by the identity pipeline
    if platform.get("protected"):
        return None

    url = render_url(platform["url_template"], username)
    probe_url = render_url(
        platform.get("probe_template") or platform["url_template"], username
    )
    site_headers: dict[str, str] = platform.get("headers", {}) or {}
    regex_check = platform.get("regex_check")
    category = platform.get("category", "other")

    e_code = int(platform.get("e_code") or 0)
    e_string = platform.get("e_string") or ""
    m_code = int(platform.get("m_code") or 0)
    m_string = platform.get("m_string") or ""
    have_two_sided = e_code > 0 and e_string and m_code > 0 and m_string

    if regex_check:
        try:
            if not re.match(regex_check, username):
                return None
        except re.error:
            pass

    merged_headers = {**_HEADERS, **site_headers}
    async with sem:
        try:
            # Redirects off: a 30x to a sign-in/homepage masks "user not
            # found" and is a major FP source.
            resp = await http.get(
                probe_url,
                headers=merged_headers,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=False,
            )
        except (httpx.HTTPError, asyncio.TimeoutError):
            return None
        except Exception:
            return None

        try:
            body = resp.text or ""
        except Exception:
            return None

    body_for_match = body[:_BODY_SCAN_BYTES]

    # Universal short-circuit: a 4xx/5xx is never an existing user, no
    # matter what error_types the catalog declared. Catches the Signal /
    # Discord.bio class of FPs where Sherlock listed errorType="message"
    # only, the upstream HTML/error message has since changed, and the
    # 404 was being interpreted as "found" because no status check ran.
    if resp.status_code >= 400:
        return None

    if have_two_sided:
        e_match = (resp.status_code == e_code) and (e_string in body_for_match)
        m_match = (resp.status_code == m_code) and (m_string in body_for_match)
        if not e_match or m_match:
            return None
    else:
        # Sherlock-style fallback (kept for catalogs lacking WMN markers):
        error_types: list[str] = platform.get("error_types", ["status_code"])
        error_messages: list[str] = platform.get("error_messages", [])
        error_codes: list[int] = platform.get("error_codes", [])
        if "status_code" in error_types:
            codes = error_codes or [404]
            if resp.status_code in codes:
                return None
        if "message" in error_types and error_messages:
            snippet = body_for_match.lower()
            for needle in error_messages:
                if needle and needle.lower() in snippet:
                    return None
            # Message-only Sherlock entry: require a positive signal to
            # avoid concluding "user exists" purely from absence-of-error.
            # A 200 response whose body never mentions the username is
            # almost always a homepage redirect or generic landing page.
            if "status_code" not in error_types:
                if username.lower() not in body_for_match.lower():
                    return None

    risk = "HIGH" if _is_high_risk(name, category) else "MEDIUM"
    return ProbeHit(site=name, url=url, category=category, risk_level=risk)


async def probe(
    username: str,
    http: httpx.AsyncClient,
    concurrency: int = _DEFAULT_CONCURRENCY,
) -> list[ProbeHit]:
    if not username:
        return []
    platforms = _select_platforms()
    if not platforms:
        return []
    sem = asyncio.Semaphore(max(1, concurrency))
    tasks = [
        asyncio.create_task(_check_one(p, username, http, sem)) for p in platforms
    ]
    hits: list[ProbeHit] = []
    for coro in asyncio.as_completed(tasks):
        try:
            result = await coro
        except Exception:
            continue
        if result is not None:
            hits.append(result)
    return hits
