"""Google-dork pattern catalog.

Translated from systems/DorkER/dorker.py (MIT). The runner is *not*
ported — we already have ``services/google_cse.py`` for paid-tier Google
search. Only the patterns are useful.

These functions return flat ``list[str]`` ready to drop into
``web_footprint`` alongside the hand-written base queries.
"""

from __future__ import annotations


def dorks_for_username(username: str) -> list[str]:
    """Username-targeted Google dorks. Skips the bare quoted-username
    query because ``web_footprint._base_queries`` already issues that."""
    if not username:
        return []
    return [
        f'"{username}" site:gitlab.com',
        f'"{username}" site:linkedin.com',
        f'"{username}" site:instagram.com',
        f'"{username}" "password"',
        f'"{username}" "credentials"',
        f'"{username}" "@gmail.com"',
        f'"{username}" "email"',
    ]


def dorks_for_email(email: str) -> list[str]:
    """Email-targeted dorks. Combines exact-match, document-type, leak,
    site-specific, and obfuscation patterns."""
    if not email or "@" not in email:
        return []
    obf_at = email.replace("@", " at ")
    obf_brackets = email.replace("@", "[at]")
    return [
        f'"{email}"',
        f'filetype:pdf "{email}"',
        f'filetype:xls "{email}"',
        f'filetype:csv "{email}"',
        f'filetype:txt "{email}"',
        f'"{email}" "password"',
        f'"{email}" "login"',
        f'site:pastebin.com "{email}"',
        f'site:github.com "{email}"',
        f'"{obf_at}"',
        f'"{obf_brackets}"',
    ]


def dorks_for_full_name(full_name: str) -> list[str]:
    """Full-name dorks beyond what ``_base_queries`` already covers."""
    if not full_name:
        return []
    return [
        f'"{full_name}" "phone"',
        f'"{full_name}" "address"',
        f'"{full_name}" filetype:pdf',
        f'"{full_name}" "resume"',
        f'"{full_name}" "cv"',
    ]
