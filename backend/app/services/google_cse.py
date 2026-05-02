"""Async Google Custom Search JSON API client.

Used by the web-footprint pipeline to run dork-style queries against the
public web. The free tier allows ~100 queries/day and Google asks for at
most one request per second; we enforce that locally with a tiny gate so
the pipeline never has to think about pacing.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx


class GoogleCSEError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Google CSE error {status_code}: {body[:300]}")
        self.status_code = status_code
        self.body = body


class GoogleCSEClient:
    """Thin async wrapper around the Google Custom Search JSON API."""

    _ENDPOINT = "https://www.googleapis.com/customsearch/v1"
    _MIN_INTERVAL_SECONDS = 1.0

    def __init__(
        self,
        api_key: str,
        cx: str,
        http: httpx.AsyncClient,
    ) -> None:
        self.api_key = api_key
        self.cx = cx
        self.http = http
        self._last_request_time: float = 0.0
        self._gate = asyncio.Lock()

    async def _respect_rate_limit(self) -> None:
        now = time.monotonic()
        delta = now - self._last_request_time
        if delta < self._MIN_INTERVAL_SECONDS:
            await asyncio.sleep(self._MIN_INTERVAL_SECONDS - delta)
        self._last_request_time = time.monotonic()

    async def search(self, query: str, num: int = 10) -> list[dict]:
        """Run a single Google CSE query.

        ``num`` is clamped to the API's [1, 10] window. Returns a list of
        result dicts with the keys the pipeline cares about: ``title``,
        ``link``, ``snippet``, ``displayLink``. Raises ``GoogleCSEError``
        on HTTP failure so the caller can decide whether to fall back or
        stop the pipeline.
        """
        if not self.api_key or not self.cx:
            raise GoogleCSEError(
                401, "GOOGLE_CSE_API_KEY or GOOGLE_CSE_CX is not configured"
            )

        clamped = max(1, min(10, int(num)))
        params: dict[str, Any] = {
            "key": self.api_key,
            "cx": self.cx,
            "q": query,
            "num": clamped,
        }

        async with self._gate:
            await self._respect_rate_limit()
            try:
                resp = await self.http.get(
                    self._ENDPOINT,
                    params=params,
                    timeout=httpx.Timeout(15.0, connect=10.0),
                )
            except httpx.HTTPError as exc:
                raise GoogleCSEError(0, f"network error: {exc}") from exc

        if resp.status_code >= 400:
            raise GoogleCSEError(resp.status_code, resp.text)

        try:
            body = resp.json()
        except ValueError as exc:
            raise GoogleCSEError(resp.status_code, f"invalid JSON: {exc}") from exc

        items = body.get("items") or []
        results: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": str(item.get("title", "") or ""),
                    "link": str(item.get("link", "") or ""),
                    "snippet": str(item.get("snippet", "") or ""),
                    "displayLink": str(item.get("displayLink", "") or ""),
                }
            )
        return results
