"""Lightweight image-enrichment helpers used by the geolocation and media
pipelines.

These deliberately avoid the heavy `media-analyzer` package — they do only
what NoDox actually needs:

* `reverse_geocode_coords(lat, lon)` — offline lookup, lat/lon → city/country
  via the `reverse-geocode` PyPI package (~1MB data shipped with the package).
* `ocr_image_bytes(image_bytes)` — best-effort Tesseract OCR. Returns the
  empty string if `pytesseract` or the Tesseract binary aren't installed.
* `extract_pii_from_text(text)` — phone/email regex pass over arbitrary text.

All three are dependency-soft: a missing package or binary disables the
feature instead of crashing the pipeline.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reverse geocoding (offline, ~1MB dataset packaged with reverse-geocode).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeoLookup:
    city: str
    country_code: str
    country: str

    def display(self) -> str:
        parts = [p for p in (self.city, self.country) if p]
        return ", ".join(parts) if parts else self.country_code or "unknown"


def reverse_geocode_coords(lat: float, lon: float) -> GeoLookup | None:
    """Return the nearest populated place to the given coordinates.

    Uses the offline `reverse-geocode` package. Returns ``None`` if the
    package isn't installed or the lookup fails.
    """
    try:
        import reverse_geocode  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("reverse-geocode package not installed; skipping lookup")
        return None
    try:
        result = reverse_geocode.search([(lat, lon)])
    except Exception as exc:
        logger.debug("reverse-geocode lookup failed: %s", exc)
        return None
    if not result:
        return None
    entry = result[0]
    return GeoLookup(
        city=str(entry.get("city", "") or ""),
        country_code=str(entry.get("country_code", "") or ""),
        country=str(entry.get("country", "") or ""),
    )


# ---------------------------------------------------------------------------
# OCR (optional, depends on Tesseract being installed on PATH).
# ---------------------------------------------------------------------------


_OCR_AVAILABILITY: bool | None = None


def ocr_available() -> bool:
    """Probe pytesseract + Tesseract binary once and cache the answer."""
    global _OCR_AVAILABILITY
    if _OCR_AVAILABILITY is not None:
        return _OCR_AVAILABILITY
    try:
        import pytesseract  # type: ignore[import-not-found]

        pytesseract.get_tesseract_version()
        _OCR_AVAILABILITY = True
    except Exception as exc:
        logger.debug("Tesseract unavailable: %s", exc)
        _OCR_AVAILABILITY = False
    return _OCR_AVAILABILITY


def ocr_image_bytes(image_bytes: bytes) -> str:
    """Run Tesseract OCR on raw image bytes. Returns "" on any failure."""
    if not ocr_available():
        return ""
    try:
        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        return ""
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            return pytesseract.image_to_string(img) or ""
    except Exception as exc:
        logger.debug("OCR failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Lightweight PII regex pass over text (used on OCR output and elsewhere).
# ---------------------------------------------------------------------------


_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
# E.164-ish + common North American formats. Deliberately loose; we surface
# matches as MEDIUM confidence, not as ground truth.
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}"
)


def extract_pii_from_text(text: str) -> list[dict[str, Any]]:
    """Return a list of ``{"type", "value"}`` dicts for emails/phones found."""
    if not text:
        return []
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for match in _EMAIL_RE.finditer(text):
        value = match.group(0).strip()
        key = ("email", value.lower())
        if value and key not in seen:
            seen.add(key)
            found.append({"type": "email", "value": value})
    for match in _PHONE_RE.finditer(text):
        value = match.group(0).strip()
        # Filter out obvious junk: pure dates, year-like sequences, etc.
        digits = re.sub(r"\D", "", value)
        if len(digits) < 7 or len(digits) > 15:
            continue
        key = ("phone", digits)
        if key not in seen:
            seen.add(key)
            found.append({"type": "phone", "value": value})
    return found
