"""Gravatar profile lookup.

Async port of ``systems/spiderfoot/modules/sfp_gravatar.py`` (MIT).
Gravatar exposes a JSON profile keyed on the MD5 of the lowercased,
trimmed email address. Free and unauthenticated.

For any breach- or dork-derived email we surface, we hash and probe
Gravatar — a 200 response means the email is publicly registered, and
the JSON often leaks a real name, additional emails, phone numbers, and
linked social accounts. ``lookup_email`` returns ``None`` on miss/error.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


_PROFILE_URL = "https://secure.gravatar.com/{hash}.json"
_USER_AGENT = "NODOXX-self-audit/0.1"
_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class GravatarLinkedAccount:
    site: str
    url: str
    username: str = ""


@dataclass(frozen=True)
class GravatarProfile:
    email: str
    preferred_username: str = ""
    full_name: str = ""
    phone_numbers: tuple[str, ...] = field(default_factory=tuple)
    extra_emails: tuple[str, ...] = field(default_factory=tuple)
    accounts: tuple[GravatarLinkedAccount, ...] = field(default_factory=tuple)
    raw_url: str = ""

    @property
    def is_empty(self) -> bool:
        return not (
            self.preferred_username
            or self.full_name
            or self.phone_numbers
            or self.extra_emails
            or self.accounts
        )


def _email_hash(email: str) -> str:
    return hashlib.md5(  # noqa: S324 — Gravatar requires md5
        email.strip().lower().encode("utf-8", errors="replace")
    ).hexdigest()


async def lookup_email(
    email: str, http: httpx.AsyncClient
) -> GravatarProfile | None:
    """Fetch a Gravatar profile for `email`. Returns ``None`` if the
    email has no Gravatar entry or if the request fails."""
    if not email or "@" not in email:
        return None

    hashed = _email_hash(email)
    url = _PROFILE_URL.format(hash=hashed)
    try:
        resp = await http.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_TIMEOUT_SECONDS,
        )
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        logger.debug("Gravatar request failed: %s", exc)
        return None

    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None

    entries = data.get("entry") if isinstance(data, dict) else None
    if not entries or not isinstance(entries, list):
        return None
    entry = entries[0]
    if not isinstance(entry, dict):
        return None

    return GravatarProfile(
        email=email,
        preferred_username=str(entry.get("preferredUsername") or "").strip(),
        full_name=_extract_full_name(entry),
        phone_numbers=_extract_phones(entry),
        extra_emails=_extract_emails(entry, exclude=email),
        accounts=_extract_accounts(entry),
        raw_url=url,
    )


def _extract_full_name(entry: dict) -> str:
    name = entry.get("name")
    if isinstance(name, dict):
        formatted = str(name.get("formatted") or "").strip()
        if formatted:
            return formatted
    if isinstance(name, list):
        for item in name:
            if isinstance(item, dict):
                formatted = str(item.get("formatted") or "").strip()
                if formatted:
                    return formatted
    return ""


def _extract_phones(entry: dict) -> tuple[str, ...]:
    out: list[str] = []
    for item in entry.get("phoneNumbers") or []:
        if isinstance(item, dict):
            value = str(item.get("value") or "").strip()
            if value:
                out.append(value)
    return tuple(out)


def _extract_emails(entry: dict, exclude: str) -> tuple[str, ...]:
    out: list[str] = []
    seen = {exclude.lower()}
    for item in entry.get("emails") or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        out.append(value)
    return tuple(out)


def _extract_accounts(entry: dict) -> tuple[GravatarLinkedAccount, ...]:
    out: list[GravatarLinkedAccount] = []
    for item in entry.get("accounts") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        site = str(item.get("shortname") or item.get("name") or "").strip()
        if not url and not site:
            continue
        out.append(
            GravatarLinkedAccount(
                site=site,
                url=url,
                username=str(item.get("username") or "").strip(),
            )
        )
    return tuple(out)
