"""searchcode.com public-code search.

Async port of ``systems/spiderfoot/modules/sfp_searchcode.py`` (MIT).
searchcode is free, unauthenticated, and rate-limited (~1 req/2s). We
use it as a non-Google pivot to surface mentions of a username or email
inside public source repositories.

Returns a list of ``CodeHit`` records — caller turns each into a
``Finding`` in ``web_footprint``.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


_API_URL = "https://searchcode.com/api/codesearch_I/"
_USER_AGENT = "NODOXX-self-audit/0.1"
_MIN_DELAY_SECONDS = 2.0
_TIMEOUT_SECONDS = 15.0
_MAX_PAGES = 2  # 100 results / page is plenty for footprinting.


_rate_lock = asyncio.Lock()
_last_request_time = 0.0


@dataclass(frozen=True)
class CodeHit:
    repo: str
    file_url: str
    snippet: str
    language: str


async def _rate_limited_get(
    http: httpx.AsyncClient, url: str
) -> httpx.Response | None:
    global _last_request_time
    async with _rate_lock:
        now = asyncio.get_event_loop().time()
        wait = _MIN_DELAY_SECONDS - (now - _last_request_time)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            resp = await http.get(
                url,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                timeout=_TIMEOUT_SECONDS,
            )
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.debug("searchcode request failed: %s", exc)
            _last_request_time = asyncio.get_event_loop().time()
            return None
        _last_request_time = asyncio.get_event_loop().time()
        return resp


async def search(
    query: str, http: httpx.AsyncClient, max_results: int = 25
) -> list[CodeHit]:
    """Search searchcode.com for `query`. Empty list on any error."""
    if not query:
        return []

    hits: list[CodeHit] = []
    page = 0
    while page < _MAX_PAGES and len(hits) < max_results:
        params = urllib.parse.urlencode(
            {"q": query, "p": page, "per_page": 100}
        )
        url = f"{_API_URL}?{params}"
        resp = await _rate_limited_get(http, url)
        if resp is None or resp.status_code != 200:
            break
        try:
            data = resp.json()
        except Exception:
            break

        results = data.get("results") or []
        if not results:
            break

        for result in results:
            if len(hits) >= max_results:
                break
            if not isinstance(result, dict):
                continue
            repo = str(result.get("repo") or "").strip()
            file_url = str(result.get("url") or "").strip()
            language = str(result.get("language") or "").strip()
            lines = result.get("lines") or {}
            snippet = ""
            if isinstance(lines, dict) and lines:
                snippet = "\n".join(
                    str(v) for v in list(lines.values())[:3]
                )[:400]
            if not repo and not file_url:
                continue
            hits.append(
                CodeHit(
                    repo=repo,
                    file_url=file_url,
                    snippet=snippet,
                    language=language,
                )
            )

        if not data.get("nextpage"):
            break
        page += 1

    return hits
