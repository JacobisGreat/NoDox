"""Email-pattern permutator.

Generates the most-likely email candidates for a target given their
full name and (optionally) known username. Pure string manipulation —
no network IO, no config. The caller is expected to validate each
candidate via Gravatar / HIBP / EmailRep so the permutator stays cheap
and side-effect-free.

Inspired by SpiderFoot's ``sfp_emailformat`` and the classic
``email-permutator`` pattern (firstname.lastname@domain etc.). We cap
output to a curated subset of patterns × providers because the
combinatorial explosion of unicode-stripped variants × every public
provider would torch HIBP / Gravatar rate limits.
"""

from __future__ import annotations

import re
import unicodedata


# Public mailbox providers worth probing. Domain order matters — Gmail
# first because it dominates personal-account share by an order of
# magnitude. Caller can early-exit once a candidate verifies.
_PUBLIC_PROVIDERS: tuple[str, ...] = (
    "gmail.com",
    "outlook.com",
    "hotmail.com",
    "yahoo.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
)


# Pattern templates use {f}=first initial, {l}=last initial, {first},
# {last}, {middle}, {user}=known username. Templates that require a
# field that's missing get skipped.
_NAME_PATTERNS: tuple[str, ...] = (
    "{first}.{last}",
    "{first}{last}",
    "{first}_{last}",
    "{first}-{last}",
    "{f}{last}",
    "{f}.{last}",
    "{first}{l}",
    "{first}.{l}",
    "{last}.{first}",
    "{last}{first}",
    "{first}",
    "{last}",
)

_USER_PATTERNS: tuple[str, ...] = (
    "{user}",
)


def _ascii_slug(value: str) -> str:
    """Strip diacritics, lowercase, keep [a-z0-9]."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", stripped.lower())


def _split_name(full_name: str) -> tuple[str, str, str]:
    """Return (first, middle, last) ASCII slugs. ``middle`` may be ''."""
    if not full_name:
        return "", "", ""
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return _ascii_slug(parts[0]), "", ""
    if len(parts) == 2:
        return _ascii_slug(parts[0]), "", _ascii_slug(parts[-1])
    return (
        _ascii_slug(parts[0]),
        _ascii_slug(parts[1]),
        _ascii_slug(parts[-1]),
    )


def generate_candidates(
    full_name: str,
    username: str = "",
    extra_domains: tuple[str, ...] = (),
    max_candidates: int = 40,
) -> list[str]:
    """Return up to ``max_candidates`` likely email addresses for the
    given identity. Order is deterministic (most-likely first) so a
    caller probing in order can early-exit on the first hit."""
    if max_candidates <= 0:
        return []

    first, _middle, last = _split_name(full_name)
    user_slug = _ascii_slug(username)

    # Build local-parts in priority order: name patterns first, then a
    # raw username fallback, then prefixed/suffixed common-noise variants.
    local_parts: list[str] = []
    seen_local: set[str] = set()

    def _push(local: str) -> None:
        if local and local not in seen_local:
            seen_local.add(local)
            local_parts.append(local)

    fields = {
        "first": first,
        "last": last,
        "f": first[:1],
        "l": last[:1],
        "user": user_slug,
    }

    for tpl in _NAME_PATTERNS:
        if not first and "{first}" in tpl:
            continue
        if not last and "{last}" in tpl:
            continue
        if not first and "{f}" in tpl:
            continue
        if not last and "{l}" in tpl:
            continue
        try:
            _push(tpl.format(**fields))
        except KeyError:
            continue

    if user_slug:
        for tpl in _USER_PATTERNS:
            try:
                _push(tpl.format(**fields))
            except KeyError:
                continue
        # Common appendix patterns observed in scraped account dumps.
        if first:
            _push(f"{user_slug}.{first}")

    # Personal/extra domains go FIRST — if a target has their own
    # domain, that's the highest-probability mailbox host.
    domains: list[str] = []
    for d in extra_domains:
        d_clean = d.strip().lower().lstrip("@")
        if d_clean and d_clean not in domains:
            domains.append(d_clean)
    for d in _PUBLIC_PROVIDERS:
        if d not in domains:
            domains.append(d)

    candidates: list[str] = []
    seen: set[str] = set()
    # Provider-major ordering: cycle local-parts × domains so Gmail
    # gets all top-priority locals before Outlook does.
    for domain in domains:
        for local in local_parts:
            email = f"{local}@{domain}"
            if email in seen:
                continue
            seen.add(email)
            candidates.append(email)
            if len(candidates) >= max_candidates:
                return candidates

    return candidates


def domain_from_url(url: str) -> str:
    """Extract a bare hostname suitable for use as an email domain.

    Used when a target's profile lists an ``external_url`` — that domain
    is the most likely place their email actually lives. Returns ``""``
    if the URL can't be parsed."""
    if not url:
        return ""
    cleaned = url.strip()
    cleaned = re.sub(r"^https?://", "", cleaned, flags=re.I)
    cleaned = cleaned.split("/", 1)[0]
    cleaned = cleaned.split("?", 1)[0]
    cleaned = cleaned.split(":", 1)[0]
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned.lower()
