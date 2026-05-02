"""HaveIBeenPwned v3 breach lookup.

Async wrapper over ``haveibeenpwned.com/api/v3/breachedaccount/{email}``,
modeled on the rate-limit pattern in
``systems/spiderfoot/modules/sfp_haveibeenpwned.py`` (MIT). HIBP's free
tier is gone — without a paid API key (~$3.95/mo), ``check_email``
returns an empty list and the caller surfaces an info-level
``pipeline_status`` event instead of a hard error.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


_API_URL = "https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
_USER_AGENT = "NODOXX-self-audit/0.1"
# HIBP enforces ~1 request / 1.5s on the cheapest paid tier.
_MIN_DELAY_SECONDS = 1.6
_TIMEOUT_SECONDS = 15.0


_rate_lock = asyncio.Lock()
_last_request_time = 0.0


@dataclass(frozen=True)
class BreachInfo:
    name: str
    title: str
    domain: str
    breach_date: str
    data_classes: tuple[str, ...]
    is_sensitive: bool
    is_verified: bool

    @property
    def critical(self) -> bool:
        sensitive_classes = {
            "Passwords",
            "Password hashes",
            "Email addresses",
            "Phone numbers",
            "Credit cards",
            "Bank account numbers",
            "Social security numbers",
        }
        return any(c in sensitive_classes for c in self.data_classes)


async def _rate_limited_get(
    http: httpx.AsyncClient, url: str, headers: dict[str, str]
) -> httpx.Response | None:
    global _last_request_time
    async with _rate_lock:
        now = asyncio.get_event_loop().time()
        wait = _MIN_DELAY_SECONDS - (now - _last_request_time)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            resp = await http.get(url, headers=headers, timeout=_TIMEOUT_SECONDS)
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.debug("HIBP request failed: %s", exc)
            _last_request_time = asyncio.get_event_loop().time()
            return None
        _last_request_time = asyncio.get_event_loop().time()
        return resp


async def check_email(
    email: str, api_key: str, http: httpx.AsyncClient
) -> list[BreachInfo]:
    """Look up `email` against HIBP. Returns an empty list on any error
    or when no breaches are found. Caller must check `api_key` truthiness
    before invoking — we don't try the free v2 fallback (deprecated)."""
    if not email or not api_key:
        return []
    url = _API_URL.format(email=email)
    headers = {
        "Accept": "application/vnd.haveibeenpwned.v3+json",
        "hibp-api-key": api_key,
        "User-Agent": _USER_AGENT,
    }

    for attempt in range(2):
        resp = await _rate_limited_get(http, url, headers)
        if resp is None:
            return []
        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                return []
            return _parse_breaches(data)
        if resp.status_code == 404:
            return []
        if resp.status_code == 401:
            logger.warning("HIBP authentication failed; check HIBP_API_KEY")
            return []
        if resp.status_code == 429 and attempt == 0:
            await asyncio.sleep(2.5)
            continue
        return []
    return []


def _parse_breaches(data: Any) -> list[BreachInfo]:
    if not isinstance(data, list):
        return []
    out: list[BreachInfo] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        out.append(
            BreachInfo(
                name=str(entry.get("Name", "") or ""),
                title=str(entry.get("Title", "") or ""),
                domain=str(entry.get("Domain", "") or ""),
                breach_date=str(entry.get("BreachDate", "") or ""),
                data_classes=tuple(
                    str(c) for c in (entry.get("DataClasses") or []) if c
                ),
                is_sensitive=bool(entry.get("IsSensitive", False)),
                is_verified=bool(entry.get("IsVerified", False)),
            )
        )
    return out
