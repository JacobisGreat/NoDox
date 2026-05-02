"""IntelBase email-intelligence lookup.

Async wrapper over ``api.intelbase.is/lookup/email``. Given an email,
IntelBase returns breach matches, linked-account discovery, and
profile-enrichment hits across hundreds of services. We treat any
non-200 response — and any structural surprise in the body — as a soft
failure and return ``IntelBaseResult.empty()`` so callers can degrade
gracefully without aborting the surrounding pipeline.

The response schema isn't formally documented as of writing. The parser
below pulls out the fields IntelBase advertises (breaches, accounts,
profile) and falls back to a single "raw" record so something is still
surfaced even if the shape shifts under us.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


_API_URL = "https://api.intelbase.is/lookup/email"
_USER_AGENT = "NODOXX-self-audit/0.1"
_TIMEOUT_SECONDS = 25.0
_RETRY_ON_429_AFTER_SECONDS = 3.0


@dataclass(frozen=True)
class IntelBaseBreach:
    name: str
    title: str
    domain: str
    breach_date: str
    data_classes: tuple[str, ...]
    description: str

    @property
    def critical(self) -> bool:
        sensitive = {
            "Passwords",
            "Password hashes",
            "Credit cards",
            "Bank account numbers",
            "Social security numbers",
            "SSN",
            "Government issued IDs",
        }
        return any(c in sensitive for c in self.data_classes)


@dataclass(frozen=True)
class IntelBaseAccount:
    """A platform/service account linked to the email."""

    site: str
    url: str
    username: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class IntelBaseResult:
    breaches: list[IntelBaseBreach] = field(default_factory=list)
    accounts: list[IntelBaseAccount] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "IntelBaseResult":
        return cls()

    @property
    def is_empty(self) -> bool:
        return not (self.breaches or self.accounts or self.profile)


async def lookup_email(
    email: str,
    api_key: str,
    http: httpx.AsyncClient,
    *,
    include_data_breaches: bool = True,
    timeout_ms: int | None = None,
) -> IntelBaseResult:
    """Look up `email` against IntelBase. Returns an empty result on any
    failure mode (network error, auth failure, rate limit, malformed body).
    Caller must check `api_key` truthiness before invoking."""
    if not email or not api_key:
        return IntelBaseResult.empty()

    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    body: dict[str, Any] = {
        "email": email,
        "include_data_breaches": include_data_breaches,
    }
    if timeout_ms is not None:
        body["timeout_ms"] = int(timeout_ms)

    for attempt in range(2):
        try:
            resp = await http.post(
                _API_URL, headers=headers, json=body, timeout=_TIMEOUT_SECONDS
            )
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.debug("IntelBase request failed: %s", exc)
            return IntelBaseResult.empty()

        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                logger.debug("IntelBase returned non-JSON body")
                return IntelBaseResult.empty()
            return _parse_response(data)
        if resp.status_code in (401, 403):
            logger.warning(
                "IntelBase authentication failed (%s); check INTELBASE_API_KEY",
                resp.status_code,
            )
            return IntelBaseResult.empty()
        if resp.status_code == 404:
            return IntelBaseResult.empty()
        if resp.status_code == 429 and attempt == 0:
            await asyncio.sleep(_RETRY_ON_429_AFTER_SECONDS)
            continue
        logger.debug("IntelBase unexpected status %s", resp.status_code)
        return IntelBaseResult.empty()

    return IntelBaseResult.empty()


# ---------------------------------------------------------------------------
# Response parsing — defensive against shape drift.
# ---------------------------------------------------------------------------


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_list_of_str(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(_coerce_str(v) for v in value if v not in (None, ""))


def _walk_for_key(obj: Any, candidates: tuple[str, ...]) -> Any:
    """Find the first list-valued field in `obj` matching any name in
    `candidates`, searching shallow keys then one level deep."""
    if isinstance(obj, dict):
        for key in candidates:
            if key in obj and isinstance(obj[key], list):
                return obj[key]
        for value in obj.values():
            if isinstance(value, dict):
                for key in candidates:
                    if key in value and isinstance(value[key], list):
                        return value[key]
    return None


def _parse_breach(entry: dict[str, Any]) -> IntelBaseBreach:
    data_class_raw = (
        entry.get("data_classes")
        or entry.get("DataClasses")
        or entry.get("dataClasses")
        or entry.get("classes")
    )
    return IntelBaseBreach(
        name=_coerce_str(entry.get("name") or entry.get("Name")),
        title=_coerce_str(entry.get("title") or entry.get("Title")),
        domain=_coerce_str(entry.get("domain") or entry.get("Domain")),
        breach_date=_coerce_str(
            entry.get("breach_date") or entry.get("BreachDate") or entry.get("date")
        ),
        data_classes=_coerce_list_of_str(data_class_raw),
        description=_coerce_str(
            entry.get("description") or entry.get("Description") or ""
        ),
    )


def _parse_account(entry: dict[str, Any]) -> IntelBaseAccount:
    site = _coerce_str(
        entry.get("site")
        or entry.get("platform")
        or entry.get("service")
        or entry.get("source")
        or entry.get("name")
    )
    url = _coerce_str(entry.get("url") or entry.get("link") or entry.get("href"))
    username = _coerce_str(
        entry.get("username") or entry.get("handle") or entry.get("user")
    )
    return IntelBaseAccount(site=site, url=url, username=username, metadata=entry)


def _parse_response(data: Any) -> IntelBaseResult:
    if not isinstance(data, dict):
        return IntelBaseResult.empty()

    breaches: list[IntelBaseBreach] = []
    raw_breaches = _walk_for_key(data, ("breaches", "data_breaches", "Breaches"))
    if isinstance(raw_breaches, list):
        for entry in raw_breaches:
            if isinstance(entry, dict):
                breaches.append(_parse_breach(entry))

    accounts: list[IntelBaseAccount] = []
    raw_accounts = _walk_for_key(
        data, ("accounts", "linked_accounts", "registrations", "modules")
    )
    if isinstance(raw_accounts, list):
        for entry in raw_accounts:
            if isinstance(entry, dict):
                accounts.append(_parse_account(entry))

    profile_raw = data.get("profile") or data.get("enrichment") or {}
    profile = profile_raw if isinstance(profile_raw, dict) else {}

    return IntelBaseResult(
        breaches=breaches, accounts=accounts, profile=profile, raw=data
    )
