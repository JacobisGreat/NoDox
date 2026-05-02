"""Streaming image downloader with a hard size cap.

Used by the identity pipeline (profile-pic comparison) and by the
geolocation pipeline (vision analysis of post images). We refuse anything
larger than ``max_bytes`` to keep token spend and memory bounded.
"""

from __future__ import annotations

import httpx


class ImageDownloadError(RuntimeError):
    pass


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
        try:
            async with self._http.stream(
                "GET",
                url,
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
