"""Streaming image downloader with a hard size cap.

Used by the identity pipeline (profile-pic comparison) and by the
geolocation pipeline (vision analysis of post images). We refuse anything
larger than ``max_bytes`` to keep token spend and memory bounded.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx


class ImageDownloadError(RuntimeError):
    pass


# Instagram's CDN hard-blocks fetches that don't look like the IG web client.
# Sending a Chrome UA + Referer to instagram.com gets us through; without
# them every cdninstagram.com / fbcdn.net request 403s and the geolocation
# pipeline silently produces zero findings.
_IG_CDN_HOST_SUFFIXES: tuple[str, ...] = (
    "cdninstagram.com",
    "fbcdn.net",
)
_IG_CDN_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.instagram.com/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
}


def _ig_cdn_headers_if_needed(url: str) -> dict[str, str] | None:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    if not host:
        return None
    for suffix in _IG_CDN_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return _IG_CDN_HEADERS
    return None


class ImageDownloader:
    def __init__(self, http: httpx.AsyncClient, max_bytes: int = 15_000_000) -> None:
        self._http = http
        self._max_bytes = int(max_bytes)

    @staticmethod
    def _guess_media_type(content_type: str | None, url: str) -> str:
        if content_type:
            ct = content_type.split(";", 1)[0].strip().lower()
            if ct.startswith("image/"):
                return ct
        u = url.lower().split("?", 1)[0]
        if u.endswith(".png"):
            return "image/png"
        if u.endswith(".gif"):
            return "image/gif"
        if u.endswith(".webp"):
            return "image/webp"
        return "image/jpeg"

    async def download(self, url: str) -> tuple[bytes, str]:
        request_headers = _ig_cdn_headers_if_needed(url)
        try:
            async with self._http.stream(
                "GET",
                url,
                headers=request_headers,
                timeout=httpx.Timeout(20.0, connect=10.0),
                follow_redirects=True,
            ) as resp:
                if resp.status_code >= 400:
                    raise ImageDownloadError(
                        f"GET {url} returned {resp.status_code}"
                    )
                content_type = resp.headers.get("content-type")
                content_length_header = resp.headers.get("content-length")
                if content_length_header:
                    try:
                        if int(content_length_header) > self._max_bytes:
                            raise ImageDownloadError(
                                f"image at {url} exceeds {self._max_bytes} bytes"
                            )
                    except ValueError:
                        pass
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > self._max_bytes:
                        raise ImageDownloadError(
                            f"image at {url} exceeds {self._max_bytes} bytes"
                        )
                media_type = self._guess_media_type(content_type, url)
                return bytes(buf), media_type
        except ImageDownloadError:
            raise
        except httpx.HTTPError as exc:
            raise ImageDownloadError(f"network error fetching {url}: {exc}") from exc
