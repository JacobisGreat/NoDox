"""Async wrapper around trafilatura for main-text extraction.

trafilatura's parser is blocking, so the actual extraction runs in a
worker thread. We use httpx for the network fetch so we share the same
connection pool and proxy/UA settings as every other outbound request
in the audit. Failures are returned as ``None`` rather than raised — the
web-footprint pipeline calls this in a tight loop and a single bad page
should never abort the run.
"""

from __future__ import annotations

import asyncio

import httpx

try:
    import trafilatura
except ImportError:  # pragma: no cover — declared in requirements.txt
    trafilatura = None  # type: ignore[assignment]


_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (NODOXX self-audit; consent-based OSINT) "
    "Python-httpx trafilatura"
)


class TrafilaturaFetcher:
    """Fetch a URL and extract its main text content."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def _fetch(self, url: str) -> str | None:
        try:
            resp = await self._http.get(
                url,
                timeout=httpx.Timeout(5.0, connect=3.0),
                follow_redirects=True,
                headers={"user-agent": _DEFAULT_USER_AGENT},
            )
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        content_type = (resp.headers.get("content-type") or "").lower()
        if content_type and "html" not in content_type and "xml" not in content_type:
            # Skip binaries / PDFs / images — trafilatura would just discard them.
            return None
        try:
            return resp.text
        except Exception:
            return None

    @staticmethod
    def _extract_sync(html: str, url: str) -> str | None:
        if trafilatura is None:
            return None
        try:
            return trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=True,
                favor_recall=True,
                no_fallback=False,
            )
        except Exception:
            return None

    async def extract_text(self, url: str, max_chars: int = 5000) -> str | None:
        """Return the cleaned main text of ``url`` or ``None`` on failure.

        ``max_chars`` is applied as a hard truncation so the pipeline can
        bound the size of any prompt built from this content.
        """
        if not url:
            return None
        html = await self._fetch(url)
        if not html:
            return None

        text = await asyncio.to_thread(self._extract_sync, html, url)
        if not text:
            return None
        text = text.strip()
        if not text:
            return None
        if max_chars and len(text) > max_chars:
            text = text[:max_chars]
        return text
