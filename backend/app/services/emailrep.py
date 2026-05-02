"""EmailRep.io reputation lookup.

Free, unauthenticated lookup of email reputation + existence signals.
Endpoint:

    GET https://emailrep.io/{email}

Returns a JSON document with fields:
    - ``reputation`` (str: none/low/medium/high)
    - ``suspicious`` (bool)
    - ``details.deliverable`` (bool — MX-confirmed)
    - ``details.profiles`` (list[str] — sites where the email is registered)
    - ``details.first_seen`` / ``last_seen`` (date)
    - ``details.data_breach`` (bool)
    - ``details.credentials_leaked`` (bool)

Anonymous tier is severely rate-limited (~10/hour per source IP) so
callers should treat ``429`` as a soft skip and budget their use to
the highest-priority emails.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


_API_URL = "https://emailrep.io/{email}"
_USER_AGENT = "NODOXX-self-audit/0.1"
_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class EmailRepResult:
    email: str
    reputation: str = ""             # none / low / medium / high
    suspicious: bool = False
    deliverable: bool = False
    data_breach: bool = False
    credentials_leaked: bool = False
    first_seen: str = ""
    last_seen: str = ""
    profiles: tuple[str, ...] = field(default_factory=tuple)
    raw_url: str = ""

    @property
    def has_signal(self) -> bool:
        """True if EmailRep returned anything useful."""
        return bool(
            self.profiles
            or self.data_breach
            or self.credentials_leaked
            or self.first_seen
            or self.deliverable
        )


async def lookup_email(
    email: str, http: httpx.AsyncClient
) -> EmailRepResult | None:
    """Return ``EmailRepResult`` or ``None`` on miss / rate-limit /
    error. The caller should treat ``None`` as "no info, don't penalise
    the email"."""
    if not email or "@" not in email:
        return None

    url = _API_URL.format(email=email.strip().lower())
    try:
        resp = await http.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_TIMEOUT_SECONDS,
        )
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        logger.debug("EmailRep request failed: %s", exc)
        return None

    if resp.status_code == 429:
        # Anonymous rate limit. Caller should stop probing further.
        logger.debug("EmailRep rate-limited; stopping further lookups.")
        return None
    if resp.status_code != 200:
        return None

    try:
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    details = data.get("details") or {}
    if not isinstance(details, dict):
        details = {}

    profiles_raw = details.get("profiles") or []
    profiles: list[str] = []
    if isinstance(profiles_raw, list):
        for p in profiles_raw:
            s = str(p or "").strip()
            if s:
                profiles.append(s)

    return EmailRepResult(
        email=email,
        reputation=str(data.get("reputation") or "").strip(),
        suspicious=bool(data.get("suspicious")),
        deliverable=bool(details.get("deliverable")),
        data_breach=bool(details.get("data_breach")),
        credentials_leaked=bool(details.get("credentials_leaked")),
        first_seen=str(details.get("first_seen") or "").strip(),
        last_seen=str(details.get("last_seen") or "").strip(),
        profiles=tuple(profiles),
        raw_url=url,
    )
