"""Async Serper.dev client.

Drop-in replacement for the Google CSE wrapper. Serper proxies real Google
results via a simple POST endpoint, free tier ships ~2500 credits on signup
and does not require a credit card. We normalize the response into the same
shape the web_footprint pipeline already consumes (``title``, ``link``,
``snippet``, ``displayLink``) so nothing else has to change.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

import httpx


class SerperError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Serper error {status_code}: {body[:300]}")
        self.status_code = status_code
        self.body = body


class SerperClient:
    """Thin async wrapper around the Serper /search endpoint."""

    _ENDPOINT = "https://google.serper.dev/search"
    _MIN_INTERVAL_SECONDS = 0.2

    def __init__(self, api_key: str, http: httpx.AsyncClient) -> None:
        self.api_key = api_key
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
        if not self.api_key:
            raise SerperError(401, "SERPER_API_KEY is not configured")

        clamped = max(1, min(20, int(num)))
        payload = {"q": query, "num": clamped}
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

        async with self._gate:
            await self._respect_rate_limit()
            try:
                resp = await self.http.post(
                    self._ENDPOINT,
                    json=payload,
                    headers=headers,
                    timeout=httpx.Timeout(15.0, connect=10.0),
                )
            except httpx.HTTPError as exc:
                raise SerperError(0, f"network error: {exc}") from exc

        if resp.status_code >= 400:
            raise SerperError(resp.status_code, resp.text)

        try:
            body = resp.json()
        except ValueError as exc:
            raise SerperError(resp.status_code, f"invalid JSON: {exc}") from exc

        items = body.get("organic") or []
        results: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            link = str(item.get("link", "") or "")
            display = ""
            if link:
                try:
                    display = urlparse(link).netloc
                except Exception:
                    display = ""
            results.append(
                {
                    "title": str(item.get("title", "") or ""),
                    "link": link,
                    "snippet": str(item.get("snippet", "") or ""),
                    "displayLink": display,
                }
            )
        return results
