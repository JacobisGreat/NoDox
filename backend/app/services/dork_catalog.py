"""Google-dork pattern catalog.

Translated from systems/DorkER/dorker.py (MIT). The runner is *not*
ported — we already have ``services/serper.py`` for web search. Only
the patterns are useful.

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
        # Forum profile shapes — most forum software exposes
        # /profile/{user}, /user/{user}, /member/{user}, /viewtopic links
        # mentioning the handle.
        f'"{username}" inurl:profile',
        f'"{username}" inurl:user',
        f'"{username}" inurl:member',
        f'"{username}" inurl:viewtopic',
        # Title-as-username — typical of platform profile pages.
        f'intitle:"{username}"',
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
    """Full-name dorks beyond what ``_base_queries`` already covers.

    Covers the OSINT families that map to "what would a stranger learn":
    family graph, residence, DOB, education detail, professional artifacts.
    """
    if not full_name:
        return []
    return [
        f'"{full_name}" "phone"',
        f'"{full_name}" "address"',
        f'"{full_name}" filetype:pdf',
        f'"{full_name}" "resume"',
        f'"{full_name}" "cv"',
        # Family graph — surfaces relatives via wedding/obituary/society
        # pages, alumni notes, etc.
        f'"{full_name}" ("husband" OR "wife" OR "spouse" OR "partner")',
        f'"{full_name}" ("son of" OR "daughter of" OR "father" OR "mother")',
        # Residence narrowing.
        f'"{full_name}" ("lives in" OR "lives at" OR "residence" OR "home address")',
        # Date of birth.
        f'"{full_name}" ("born" OR "DOB" OR "date of birth")',
        # Education detail beyond the bare site:*.edu sweep.
        f'"{full_name}" ("graduated" OR "alumni" OR "thesis" OR "dissertation")',
        # Title-as-name — landing pages for that person specifically.
        f'intitle:"{full_name}"',
    ]


def dorks_for_phone(phone: str) -> list[str]:
    """Phone-targeted dorks. Generates 5 common format variants from the
    raw digits and runs them as exact-match queries plus a couple of
    directory-style pivots. Caller should pre-validate that the phone is
    plausible (≥10 digits)."""
    if not phone:
        return []
    digits = "".join(ch for ch in phone if ch.isdigit())
    # Tolerate +1 country-code prefix.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return []
    a, b, c = digits[:3], digits[3:6], digits[6:10]
    variants = [
        f"+1 ({a}) {b}-{c}",
        f"({a}) {b}-{c}",
        f"{a}-{b}-{c}",
        f"{a}.{b}.{c}",
        f"{a}{b}{c}",
    ]
    queries = [f'"{v}"' for v in variants]
    queries.extend(
        [
            f'"{variants[0]}" inurl:directory',
            f'"{variants[0]}" "address"',
            f'"{variants[0]}" "name"',
        ]
    )
    return queries
