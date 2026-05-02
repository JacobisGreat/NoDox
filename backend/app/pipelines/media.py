"""Media analysis pipeline.

Looks for visible PII inside post images that the geolocation pipeline
doesn't already cover:

* OCR (Tesseract) → phone/email regex pass → Findings
* Future-friendly hook for object detection / additional channels.

Runs in parallel with geolocation. Skips silently if no posts are loaded
or Tesseract isn't installed.

Heavy ML modules from the upstream `media-analyzer` project (BLIP captions,
InsightFace, OpenCLIP, ResNet) are deliberately NOT used here — they
require gigabytes of weights, a non-commercial face-detection model, and
external binaries. We vendor only the lightweight pieces we need into
``services/media_enrichment.py``.
"""

from __future__ import annotations

import asyncio
import logging

from app.schemas.events import (
    finding as _finding_event,
    pipeline_status as _pipeline_status_event,
)
from app.schemas.findings import Finding
from app.services.media_enrichment import (
    extract_pii_from_text,
    ocr_available,
    ocr_image_bytes,
)
from app.session_store import SessionState

logger = logging.getLogger(__name__)

_PIPELINE = "media"
_MAX_POSTS = 20
_OCR_CONCURRENCY = 2


def _shortcode_url(shortcode: str) -> str:
    return f"https://instagram.com/p/{shortcode}"


async def _emit_finding(session: SessionState, finding: Finding) -> None:
    payload = finding.to_dict()
    findings_log: list[dict] = session.data.setdefault("findings", [])
    tagged = dict(payload)
    tagged["_pipeline"] = _PIPELINE
    findings_log.append(tagged)
    await session.publish_event(_finding_event(_PIPELINE, payload))


async def _process_post(session: SessionState, post: dict, sem: asyncio.Semaphore) -> None:
    image_url = post.get("image_url")
    if not image_url:
        return
    downloader = session.data.get("image_downloader")
    if downloader is None:
        return
    shortcode = post.get("shortcode") or "unknown"

    try:
        image_bytes, _ = await downloader.download(image_url)
    except Exception:
        return

    async with sem:
        text = await asyncio.to_thread(ocr_image_bytes, image_bytes)
    if not text or not text.strip():
        return

    pii_items = extract_pii_from_text(text)
    if not pii_items:
        return

    for item in pii_items:
        pii_type = item.get("type", "unknown")
        value = item.get("value", "")
        if not value:
            continue
        risk = "HIGH" if pii_type in ("email", "phone") else "MEDIUM"
        finding = Finding(
            source="media:ocr_pii",
            evidence_chain=[
                f"Post: {_shortcode_url(shortcode)}",
                f"OCR found {pii_type} visible in image: {value}",
            ],
            confidence=0.7,
            risk_level=risk,  # type: ignore[arg-type]
            remediation=(
                f"Open the post at {_shortcode_url(shortcode)} and crop or "
                f"redact the area where the {pii_type} appears, or delete "
                "the post entirely. Visible PII in images survives caption "
                "edits."
            ),
            metadata={
                "shortcode": shortcode,
                "pii_type": pii_type,
                "pii_value": value,
            },
        )
        await _emit_finding(session, finding)


async def run(session: SessionState) -> None:
    await session.publish_event(_pipeline_status_event(_PIPELINE, "running"))

    try:
        if not ocr_available():
            await session.publish_event(
                _pipeline_status_event(
                    _PIPELINE,
                    "complete",
                    "Tesseract OCR not installed; skipping image-PII scan.",
                )
            )
            return

        posts: list[dict] = list(session.data.get("posts") or [])
        image_posts = [p for p in posts if not p.get("is_video")][:_MAX_POSTS]
        if not image_posts:
            await session.publish_event(
                _pipeline_status_event(_PIPELINE, "complete", "No images to scan.")
            )
            return

        sem = asyncio.Semaphore(_OCR_CONCURRENCY)
        tasks = [
            asyncio.create_task(_process_post(session, post, sem))
            for post in image_posts
        ]
        # Per-post failures must not abort the pipeline.
        await asyncio.gather(*tasks, return_exceptions=True)

        await session.publish_event(_pipeline_status_event(_PIPELINE, "complete"))
    except Exception as exc:  # noqa: BLE001 — defensive top-level guard
        await session.publish_event(
            _pipeline_status_event(_PIPELINE, "error", f"{type(exc).__name__}: {exc}")
        )
