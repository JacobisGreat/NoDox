"""Loader for static JSON assets (Sherlock-derived platform list).

Source of truth: ``sherlock_data.json`` — vendored verbatim from the
upstream Sherlock project (see ``systems/sherlock/.../resources/data.json``).
Each site uses one of three detection methods: ``status_code``,
``message`` (string in body), or ``response_url`` (final URL after
redirects equals an error URL).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent
_SHERLOCK_FILE = _DATA_DIR / "sherlock_data.json"

# Inferred categories — used only for remediation copy and high-risk
# weighting. Substring match against the lower-cased site name.
_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    # commerce / marketplaces (matched first so 'ebay' isn't mislabeled social)
    ("ebay", "commerce"),
    ("etsy", "commerce"),
    ("shop", "commerce"),
    ("market", "commerce"),
    ("mercado", "commerce"),
    # messaging / contact-by-handle
    ("telegram", "messaging"),
    ("signal", "messaging"),
    ("kik", "messaging"),
    ("skype", "messaging"),
    ("whatsapp", "messaging"),
    ("discord", "messaging"),
    # professional networks
    ("linkedin", "professional"),
    ("xing", "professional"),
    ("behance", "professional"),
    ("dribbble", "professional"),
    ("angel", "professional"),
    # development
    ("github", "development"),
    ("gitlab", "development"),
    ("bitbucket", "development"),
    ("npm", "development"),
    ("pypi", "development"),
    ("crates", "development"),
    ("docker", "development"),
    ("hackthebox", "development"),
    ("hackerrank", "development"),
    ("leetcode", "development"),
    ("codeforces", "development"),
    ("codepen", "development"),
    ("codewars", "development"),
    ("replit", "development"),
    ("stack", "development"),
    ("dev.to", "development"),
    ("devto", "development"),
    ("hashnode", "development"),
    ("topcoder", "development"),
    ("hackerearth", "development"),
    ("kaggle", "development"),
    ("sourceforge", "development"),
    ("gitea", "development"),
    ("codeberg", "development"),
]

# Bumped to HIGH risk if the user is matched on one of these.
HIGH_RISK_PLATFORMS = {
    "LinkedIn",
    "GitHub",
    "Reddit",
    "Facebook",
    "Twitter",
    "X",
    "TikTok",
}


def _infer_category(name: str, tags: list[str] | None) -> str:
    if tags:
        if "adult" in tags:
            return "adult"
        if "gaming" in tags:
            return "gaming"
    lname = name.lower()
    for needle, cat in _CATEGORY_KEYWORDS:
        if needle in lname:
            return cat
    return "social_media"


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int)) and str(v)]
    return []


def _coerce_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        out: list[int] = []
        for v in value:
            try:
                out.append(int(v))
            except (TypeError, ValueError):
                continue
        return out
    try:
        return [int(value)]
    except (TypeError, ValueError):
        return []


def _coerce_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _normalize_template(template: str) -> str:
    """Convert Sherlock's ``{}`` placeholder to ``{username}`` so
    ``render_url`` can substitute either format uniformly."""
    if not template:
        return ""
    if "{username}" in template:
        return template
    return template.replace("{}", "{username}")


def _normalize_error_types(value: Any) -> list[str]:
    """Schema allows either a string or an array of strings."""
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    return []


def _validate_regex(pattern: str) -> str | None:
    """Ensure the per-site username regex is compilable; otherwise drop it."""
    try:
        re.compile(pattern)
        return pattern
    except re.error:
        return None


@lru_cache(maxsize=1)
def load_platforms() -> list[dict[str, Any]]:
    """Return the normalized platform descriptor list.

    Each entry::

        {
            "name": str,
            "url_template": str,         # display URL with "{username}"
            "probe_template": str,       # what we actually GET (== url_template if no urlProbe)
            "method": "GET" | "POST" | "HEAD" | "PUT",
            "headers": dict[str, str],
            "request_payload": dict | None,
            "error_types": [str, ...],   # subset of {message, status_code, response_url}
            "error_messages": [str, ...],
            "error_codes": [int, ...],
            "error_url_template": str,   # error redirect target (for response_url)
            "regex_check": str | None,
            "category": str,
            "nsfw": bool,
            "username_claimed": str,
            "tags": [str, ...],
        }
    """
    if not _SHERLOCK_FILE.exists():
        return []
    raw = json.loads(_SHERLOCK_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return []

    platforms: list[dict[str, Any]] = []
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        # Skip the JSON Schema pointer key.
        if name.startswith("$"):
            continue
        url_template = _normalize_template(_coerce_str(entry.get("url")))
        if not url_template:
            continue
        # url_template must hold the canonical user URL (display). For POST
        # sites (Anilist, Discord, Holopin), the username goes into the
        # request_payload — but the display URL still has {username}.
        if "{username}" not in url_template:
            continue

        error_types = _normalize_error_types(entry.get("errorType"))
        # Default to status_code if none declared (Sherlock's implicit default).
        if not error_types:
            error_types = ["status_code"]

        url_probe_raw = _coerce_str(entry.get("urlProbe"))
        probe_template = (
            _normalize_template(url_probe_raw) if url_probe_raw else url_template
        )

        regex_check_raw = entry.get("regexCheck")
        regex_check = (
            _validate_regex(regex_check_raw)
            if isinstance(regex_check_raw, str) and regex_check_raw
            else None
        )

        headers = entry.get("headers")
        if not isinstance(headers, dict):
            headers = {}

        request_payload = entry.get("request_payload")
        if not isinstance(request_payload, dict):
            request_payload = None

        tags_raw = entry.get("tags")
        if isinstance(tags_raw, str):
            tags: list[str] = [tags_raw]
        elif isinstance(tags_raw, list):
            tags = [str(t) for t in tags_raw if isinstance(t, str)]
        else:
            tags = []

        method = _coerce_str(entry.get("request_method"), "GET").upper() or "GET"

        platforms.append(
            {
                "name": str(name),
                "url_template": url_template,
                "probe_template": probe_template,
                "method": method,
                "headers": {str(k): str(v) for k, v in headers.items()},
                "request_payload": request_payload,
                "error_types": error_types,
                "error_messages": _coerce_str_list(entry.get("errorMsg")),
                "error_codes": _coerce_int_list(entry.get("errorCode")),
                "error_url_template": _normalize_template(
                    _coerce_str(entry.get("errorUrl"))
                ),
                "regex_check": regex_check,
                "category": _infer_category(name, tags),
                "nsfw": bool(entry.get("isNSFW", False)),
                "username_claimed": _coerce_str(entry.get("username_claimed")),
                "tags": tags,
            }
        )
    return platforms


def render_url(template: str, username: str) -> str:
    """Substitute ``{username}`` into a URL template."""
    if not template:
        return ""
    return template.replace("{username}", username)
