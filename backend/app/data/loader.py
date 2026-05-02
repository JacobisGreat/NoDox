"""Loader for the platform catalog — union of WhatsMyName + Sherlock.

We read BOTH:
- ``wmn-data.json`` — WhatsMyName / blackbird's data source. Two-sided
  detection (``e_code``+``e_string`` AND NOT ``m_string``). ~700 sites,
  daily-updated, CC-BY-SA 4.0.
- ``sherlock_data.json`` — Sherlock project catalog. ~478 sites with
  three-mode detection (status_code / message / response_url). MIT.

When a site appears in BOTH, **WMN wins** (richer signal). When a site is
only in one, we use that one's rules. The matcher in
``pipelines/identity.py::_is_claimed`` already handles both schemas, so
each platform carries the fields appropriate to its source — WMN entries
have ``e_code``/``e_string``/``m_code``/``m_string``; Sherlock-only
entries have ``error_types``/``error_messages``/``error_codes``.

This union exists to maximize cross-reference coverage. WMN alone misses
~296 sites Sherlock catalogs (1337x, Academia.edu, Apple Developer,
Archive.org, AniWorld, Aparat, …); Sherlock alone misses ~549 WMN sites.

See ``IDENTITY_FALSE_POSITIVES.md`` for the false-positive rationale.
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
_SHERLOCK_FILE = _DATA_DIR / "sherlock_data.json"

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
                "_source": "wmn",
            }
        )

    # ---- Layer in Sherlock-only sites for coverage ----
    _merge_sherlock_only(platforms)
    return platforms


_SHERLOCK_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("ebay", "commerce"),
    ("etsy", "commerce"),
    ("shop", "commerce"),
    ("market", "commerce"),
    ("telegram", "messaging"),
    ("signal", "messaging"),
    ("discord", "messaging"),
    ("linkedin", "professional"),
    ("xing", "professional"),
    ("behance", "professional"),
    ("github", "development"),
    ("gitlab", "development"),
    ("stack", "development"),
    ("hackerrank", "development"),
    ("leetcode", "development"),
    ("codepen", "development"),
    ("codewars", "development"),
    ("replit", "development"),
    ("docker", "development"),
    ("npm", "development"),
    ("pypi", "development"),
]


def _infer_sherlock_category(name: str) -> str:
    lname = name.lower()
    for needle, cat in _SHERLOCK_CATEGORY_KEYWORDS:
        if needle in lname:
            return cat
    return "social_media"


def _merge_sherlock_only(platforms: list[dict[str, Any]]) -> None:
    """Append Sherlock entries whose name isn't already present from WMN.

    WMN's two-sided detection is strictly stronger, so any name overlap
    keeps the WMN entry. This pass extends coverage to ~300 sites WMN
    doesn't catalog (1337x, Academia.edu, Archive.org, AniWorld, …).
    """
    if not _SHERLOCK_FILE.exists():
        return
    try:
        sherlock = json.loads(_SHERLOCK_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("sherlock_data.json unreadable: %s", exc)
        return
    if not isinstance(sherlock, dict):
        return

    have = {p["name"].lower() for p in platforms}

    for name, entry in sherlock.items():
        if name.startswith("$") or not isinstance(entry, dict):
            continue
        if name.lower() in have:
            continue  # WMN wins on overlap

        url_raw = _coerce_str(entry.get("url"))
        if not url_raw:
            continue
        # Sherlock uses ``{}`` placeholder; convert to {username}.
        url_template = (
            url_raw if "{username}" in url_raw else url_raw.replace("{}", "{username}")
        )
        if "{username}" not in url_template:
            continue

        url_probe = _coerce_str(entry.get("urlProbe"))
        probe_template = (
            (
                url_probe
                if "{username}" in url_probe
                else url_probe.replace("{}", "{username}")
            )
            if url_probe
            else url_template
        )

        # Normalize errorType to a list.
        et_raw = entry.get("errorType")
        if isinstance(et_raw, str):
            error_types = [et_raw.strip()] if et_raw.strip() else []
        elif isinstance(et_raw, list):
            error_types = [str(e).strip() for e in et_raw if str(e).strip()]
        else:
            error_types = []
        if not error_types:
            error_types = ["status_code"]

        # errorMsg may be string or list.
        em_raw = entry.get("errorMsg")
        if isinstance(em_raw, str):
            error_messages = [em_raw] if em_raw else []
        elif isinstance(em_raw, list):
            error_messages = [str(m) for m in em_raw if m]
        else:
            error_messages = []

        # errorCode may be int or list.
        ec_raw = entry.get("errorCode")
        if isinstance(ec_raw, int):
            error_codes = [ec_raw]
        elif isinstance(ec_raw, list):
            error_codes = []
            for c in ec_raw:
                try:
                    error_codes.append(int(c))
                except (TypeError, ValueError):
                    continue
        else:
            error_codes = []

        # Skip Sherlock-only entries with no body match AND no explicit
        # error-code list — those are pure 200-OK guessers and are the
        # dominant FP class (see IDENTITY_FALSE_POSITIVES.md). When WMN
        # has the same site we already kept that one.
        if not error_messages and not error_codes:
            continue

        regex_check_raw = entry.get("regexCheck")
        regex_check = (
            _validate_regex(regex_check_raw)
            if isinstance(regex_check_raw, str) and regex_check_raw
            else None
        )

        method = _coerce_str(entry.get("request_method"), "GET").upper() or "GET"
        request_payload = entry.get("request_payload")
        if not isinstance(request_payload, dict):
            request_payload = None

        headers_raw = entry.get("headers")
        headers = (
            {str(k): str(v) for k, v in headers_raw.items()}
            if isinstance(headers_raw, dict)
            else {}
        )

        platforms.append(
            {
                "name": str(name),
                "url_template": url_template,
                "probe_template": probe_template,
                "method": method,
                "headers": headers,
                "request_payload": request_payload,
                # No WMN two-sided rules — leave the e_/m_ fields zero so
                # the matcher falls back to Sherlock 3-mode for these.
                "e_code": 0,
                "e_string": "",
                "m_code": 0,
                "m_string": "",
                "protected": False,
                "regex_check": regex_check,
                "category": _infer_sherlock_category(str(name)),
                "nsfw": bool(entry.get("isNSFW", False)),
                "known": (
                    [_coerce_str(entry.get("username_claimed"))]
                    if entry.get("username_claimed")
                    else []
                ),
                "tags": [],
                "error_types": error_types,
                "error_messages": error_messages,
                "error_codes": error_codes,
                "error_url_template": _coerce_str(entry.get("errorUrl")),
                "_source": "sherlock",
            }
        )


def render_url(template: str, username: str) -> str:
    """Substitute ``{username}`` into a URL template."""
    if not template:
        return ""
    return template.replace("{username}", username)
