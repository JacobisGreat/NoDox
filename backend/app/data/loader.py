"""Loader for the WhatsMyName platform catalog.

Source of truth: ``wmn-data.json`` — vendored from the WhatsMyName project
(https://github.com/WebBreacher/WhatsMyName, CC-BY-SA 4.0). The catalog
ships ~700+ sites with explicit two-sided detection (``e_code`` +
``e_string`` for "found"; ``m_code`` + ``m_string`` for "missing"). This
schema is the OSINT community's answer to Sherlock's
status-code-only false-positive problem — see
``IDENTITY_FALSE_POSITIVES.md`` for the rationale.

The previous Sherlock-derived ``sherlock_data.json`` is kept on disk for
one release as a roll-back path; the loader reads WMN exclusively.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent
_WMN_FILE = _DATA_DIR / "wmn-data.json"

# Inferred categories — used by remediation copy and high-risk weighting.
# WMN exposes a ``cat`` field but its taxonomy doesn't quite match ours.
_CATEGORY_MAP: dict[str, str] = {
    "social": "social_media",
    "coding": "development",
    "tech": "development",
    "gaming": "gaming",
    "music": "media",
    "video": "media",
    "photo": "media",
    "art": "media",
    "blog": "media",
    "shopping": "commerce",
    "finance": "commerce",
    "dating": "social_media",
    "news": "media",
    "hobby": "hobby",
    "misc": "other",
    "xx NSFW xx": "adult",
    "political": "social_media",
    "health": "other",
    "sports": "hobby",
    "education": "professional",
    "religious": "other",
    "archived": "other",
}

# Bumped to HIGH risk if the user is matched on one of these.
HIGH_RISK_PLATFORMS = {
    "LinkedIn",
    "GitHub",
    "Reddit",
    "Facebook",
    "Twitter",
    "X",
    "TikTok",
    "Instagram",
}


def _coerce_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_template(template: str) -> str:
    """Convert WMN's ``{account}`` placeholder to ``{username}`` so
    ``render_url`` can substitute uniformly with NoDox's other modules."""
    if not template:
        return ""
    if "{username}" in template:
        return template
    return template.replace("{account}", "{username}")


def _validate_regex(pattern: str) -> str | None:
    try:
        re.compile(pattern)
        return pattern
    except re.error:
        return None


def _infer_category(wmn_cat: str) -> str:
    if not wmn_cat:
        return "other"
    return _CATEGORY_MAP.get(wmn_cat, "other")


def _strip_bad_char_to_regex(strip_chars: Any) -> str | None:
    """WMN's ``strip_bad_char`` lists characters that *must not* appear in
    the username for the probe to succeed. Convert to a permissive regex
    that rejects usernames containing any of them."""
    if not strip_chars:
        return None
    if isinstance(strip_chars, str):
        chars = list(strip_chars)
    elif isinstance(strip_chars, list):
        chars = [str(c) for c in strip_chars if c]
    else:
        return None
    if not chars:
        return None
    escaped = "".join(re.escape(c) for c in chars)
    return f"^[^{escaped}]+$"


@lru_cache(maxsize=1)
def load_platforms() -> list[dict[str, Any]]:
    """Return the normalized platform descriptor list.

    Each entry::

        {
            "name": str,
            "url_template": str,         # display URL with "{username}"
            "probe_template": str,       # what we GET (== url_template if no urlProbe)
            "method": "GET" | "POST",
            "headers": dict[str, str],
            "request_payload": dict | None,
            # Two-sided detection (WhatsMyName schema):
            "e_code": int,
            "e_string": str,
            "m_code": int,
            "m_string": str,
            # WAF/CAPTCHA-fronted; matchers MUST treat as inconclusive
            # rather than emitting positive findings.
            "protected": bool,
            "regex_check": str | None,
            "category": str,
            "nsfw": bool,
            "known": [str, ...],
            "tags": [str, ...],
            # ---- Sherlock-compat fields, derived from WMN for legacy
            # callers (account_probe, etc.). New code should use the
            # two-sided fields above. ----
            "error_types": [str, ...],
            "error_messages": [str, ...],
            "error_codes": [int, ...],
            "error_url_template": str,
        }
    """
    if not _WMN_FILE.exists():
        logger.error("wmn-data.json not found at %s", _WMN_FILE)
        return []
    raw = json.loads(_WMN_FILE.read_text(encoding="utf-8"))
    sites = raw.get("sites") if isinstance(raw, dict) else None
    if not isinstance(sites, list):
        return []

    platforms: list[dict[str, Any]] = []
    for entry in sites:
        if not isinstance(entry, dict):
            continue
        name = _coerce_str(entry.get("name")).strip()
        if not name:
            continue
        uri_check = _coerce_str(entry.get("uri_check"))
        if not uri_check:
            continue

        e_code = _coerce_int(entry.get("e_code"), 0)
        m_code = _coerce_int(entry.get("m_code"), 0)
        e_string = _coerce_str(entry.get("e_string"))
        m_string = _coerce_str(entry.get("m_string"))

        # Both sides of the matcher must have signal or the entry is junk.
        if not e_string or not m_string or e_code <= 0 or m_code <= 0:
            continue

        post_body_raw = entry.get("post_body")
        method = "POST" if post_body_raw else "GET"

        probe_template = _normalize_template(uri_check)
        # Pretty URL is for display when WMN's uri_check is an API endpoint.
        uri_pretty = _coerce_str(entry.get("uri_pretty"))
        url_template = _normalize_template(uri_pretty) if uri_pretty else probe_template
        # POST sites: probe_template is a fixed endpoint without {username};
        # url_template should still resolve to a user-visible page.
        if "{username}" not in url_template:
            url_template = probe_template

        request_payload: dict[str, Any] | None = None
        if isinstance(post_body_raw, dict):
            request_payload = post_body_raw
        elif isinstance(post_body_raw, str) and post_body_raw:
            # Some WMN entries serialize POST bodies as raw form-data
            # strings. Translate to a dict for httpx ``json=`` / ``data=``.
            request_payload = {"_raw": post_body_raw}

        headers_raw = entry.get("headers")
        headers = (
            {str(k): str(v) for k, v in headers_raw.items()}
            if isinstance(headers_raw, dict)
            else {}
        )

        regex_check = _strip_bad_char_to_regex(entry.get("strip_bad_char"))
        if regex_check:
            regex_check = _validate_regex(regex_check)

        cat_raw = _coerce_str(entry.get("cat"))
        category = _infer_category(cat_raw)
        nsfw = cat_raw == "xx NSFW xx" or category == "adult"

        protected = bool(entry.get("protection"))

        known_raw = entry.get("known")
        known = (
            [str(k) for k in known_raw if isinstance(k, str) and k]
            if isinstance(known_raw, list)
            else []
        )

        # Derive Sherlock-compat fields so legacy call-sites continue to
        # work during the cutover. The two-sided fields above are
        # authoritative; everything below is a downgrade.
        error_types = ["status_code", "message"]
        error_codes = [m_code]
        error_messages = [m_string]

        platforms.append(
            {
                "name": name,
                "url_template": url_template,
                "probe_template": probe_template,
                "method": method,
                "headers": headers,
                "request_payload": request_payload,
                # Two-sided fields (the new authoritative ones):
                "e_code": e_code,
                "e_string": e_string,
                "m_code": m_code,
                "m_string": m_string,
                "protected": protected,
                "regex_check": regex_check,
                "category": category,
                "nsfw": nsfw,
                "known": known,
                "tags": [],
                # Sherlock-compat (derived):
                "error_types": error_types,
                "error_messages": error_messages,
                "error_codes": error_codes,
                "error_url_template": "",
            }
        )

    return platforms


def render_url(template: str, username: str) -> str:
    """Substitute ``{username}`` into a URL template."""
    if not template:
        return ""
    return template.replace("{username}", username)
