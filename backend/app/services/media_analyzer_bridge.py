"""Bridge to the vendored ``media-analyzer`` package in ``systems/``.

The full ``media-analyzer`` orchestrator (``MediaAnalyzer``) pulls in 25+
heavy ML deps — ``insightface``, ``opencv``, ``onnxruntime-gpu``,
``meteostat``, ``pyexiftool`` — most of which are irrelevant to
geolocation and one of which (InsightFace) is non-commercial-licensed.

We don't need the full pipeline. For location signal we want:

* **OCR** — reads street signs, store names, transit stations, license
  plates straight off the image. Often catches what the VLM misses on
  small print.
* **Object detection (DETR)** — surfaces visible objects whose labels
  sometimes carry locale-specific signal (e.g. "double-decker bus",
  "fire hydrant", specific vehicle classes).

Both modules live under ``machine_learning/`` and only depend on torch,
transformers, PIL, and pytesseract — all already in ``requirements.txt``.
This module adds ``systems/media-analyzer/src`` to ``sys.path`` so the
imports resolve, then exposes a simple async-friendly façade. Heavy
loading is lazy + soft-failing: if anything goes wrong the channel
silently disables and the rest of the geolocation pipeline keeps going.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# sys.path + package-init bypass — done once at import.
#
# media-analyzer's top-level ``__init__.py`` aggressively imports the whole
# zoo (BLIP captioner, MiniCPM, OpenAI client, InsightFace, OpenCLIP,
# material-color-utilities, …). We only want OCR + DETR object detection,
# both of which live under ``media_analyzer.machine_learning.*`` and have
# no real dependency on those heavy modules. Stubbing the top-level package
# in ``sys.modules`` BEFORE any submodule import skips ``__init__.py``
# entirely; the subpackages are namespace packages (no ``__init__.py``) so
# they resolve via the parent package's ``__path__``.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MEDIA_ANALYZER_SRC = _REPO_ROOT / "systems" / "media-analyzer" / "src"


def _install_media_analyzer_stub() -> bool:
    """Insert a lightweight ``media_analyzer`` package stub into sys.modules
    so submodule imports don't trigger the heavy package __init__. Returns
    True if the stub is in place, False if the source tree wasn't found."""
    if not _MEDIA_ANALYZER_SRC.is_dir():
        return False
    src_str = str(_MEDIA_ANALYZER_SRC)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)

    pkg_dir = _MEDIA_ANALYZER_SRC / "media_analyzer"
    if not pkg_dir.is_dir():
        return False

    existing = sys.modules.get("media_analyzer")
    # Only stub if the real package hasn't already been imported elsewhere.
    if existing is not None and getattr(existing, "__file__", None):
        return True

    import types

    stub = types.ModuleType("media_analyzer")
    stub.__path__ = [str(pkg_dir)]  # type: ignore[attr-defined]
    sys.modules["media_analyzer"] = stub
    return True


_STUB_INSTALLED = _install_media_analyzer_stub()


# ---------------------------------------------------------------------------
# Public types.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float


@dataclass(frozen=True)
class DetectedObject:
    label: str
    confidence: float


@dataclass(frozen=True)
class MediaAnalysis:
    ocr_lines: list[OCRLine] = field(default_factory=list)
    objects: list[DetectedObject] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.ocr_lines or self.objects)

    @property
    def ocr_text(self) -> str:
        return "\n".join(line.text for line in self.ocr_lines if line.text)


# ---------------------------------------------------------------------------
# Lazy loaders. Each module is independent — OCR can work even if object
# detection failed to load, and vice versa.
# ---------------------------------------------------------------------------


_DISABLED = os.environ.get("NODOX_MEDIA_ANALYZER_DISABLED", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

_ocr: Any = None
_ocr_load_attempted = False
_ocr_load_error: str | None = None
_ocr_lock = asyncio.Lock()

_objdet: Any = None
_objdet_load_attempted = False
_objdet_load_error: str | None = None
_objdet_lock = asyncio.Lock()

# Serialize forward passes — the underlying torch models aren't thread-safe
# under the hood, and we don't want to swamp CPU/GPU with concurrent calls.
_inference_semaphore = asyncio.Semaphore(1)


def _load_ocr_sync() -> Any:
    """Instantiate ResnetTesseractOCR — uses Tesseract + a ResNet detector."""
    from media_analyzer.machine_learning.ocr.resnet_tesseract_ocr import (
        ResnetTesseractOCR,
    )

    return ResnetTesseractOCR()


def _load_objdet_sync() -> Any:
    """Instantiate ResnetObjectDetection — DETR via transformers."""
    from media_analyzer.machine_learning.object_detection.resnet_object_detection import (
        ResnetObjectDetection,
    )

    return ResnetObjectDetection()


async def _get_ocr() -> Any | None:
    if _DISABLED:
        return None
    global _ocr, _ocr_load_attempted, _ocr_load_error
    if _ocr is not None or _ocr_load_attempted:
        return _ocr
    async with _ocr_lock:
        if _ocr is not None or _ocr_load_attempted:
            return _ocr
        _ocr_load_attempted = True
        try:
            _ocr = await asyncio.to_thread(_load_ocr_sync)
        except Exception as exc:
            _ocr_load_error = str(exc)
            logger.info("media_analyzer OCR unavailable: %s", exc)
            _ocr = None
    return _ocr


async def _get_objdet() -> Any | None:
    if _DISABLED:
        return None
    global _objdet, _objdet_load_attempted, _objdet_load_error
    if _objdet is not None or _objdet_load_attempted:
        return _objdet
    async with _objdet_lock:
        if _objdet is not None or _objdet_load_attempted:
            return _objdet
        _objdet_load_attempted = True
        try:
            _objdet = await asyncio.to_thread(_load_objdet_sync)
        except Exception as exc:
            _objdet_load_error = str(exc)
            logger.info("media_analyzer object detection unavailable: %s", exc)
            _objdet = None
    return _objdet


def availability() -> dict[str, Any]:
    """Snapshot of which channels are usable. Useful for /health diagnostics."""
    return {
        "disabled_via_env": _DISABLED,
        "ocr_loaded": _ocr is not None,
        "ocr_load_attempted": _ocr_load_attempted,
        "ocr_load_error": _ocr_load_error,
        "objdet_loaded": _objdet is not None,
        "objdet_load_attempted": _objdet_load_attempted,
        "objdet_load_error": _objdet_load_error,
    }


async def warmup() -> None:
    """Pre-load OCR + object detection so the first audit doesn't pay the
    cold-start cost. Safe to call repeatedly. Failures are swallowed."""
    if _DISABLED:
        return
    await asyncio.gather(_get_ocr(), _get_objdet(), return_exceptions=True)


# ---------------------------------------------------------------------------
# OCR / object detection inference helpers.
# ---------------------------------------------------------------------------


def _open_image_sync(image_bytes: bytes) -> Any | None:
    try:
        from PIL import Image as PILImage

        img = PILImage.open(io.BytesIO(image_bytes))
        # Force-load + convert so we don't return a partially-loaded handle.
        return img.convert("RGB")
    except Exception as exc:
        logger.debug("media_analyzer: failed to open image: %s", exc)
        return None


def _run_ocr_sync(model: Any, pil_image: Any) -> list[OCRLine]:
    try:
        if not model.has_legible_text(pil_image):
            return []
    except Exception as exc:
        logger.debug("media_analyzer: has_legible_text failed: %s", exc)
        # Don't bail — fall through and try OCR anyway.
    try:
        boxes = model.get_boxes(pil_image, ("eng",))
    except Exception as exc:
        logger.debug("media_analyzer: get_boxes failed: %s", exc)
        return []
    out: list[OCRLine] = []
    for box in boxes:
        try:
            text = (box.text or "").strip()
            conf = float(box.confidence)
        except Exception:
            continue
        if not text or len(text) < 2 or conf < 0.4:
            continue
        out.append(OCRLine(text=text, confidence=conf))
    return out


def _run_objdet_sync(model: Any, pil_image: Any) -> list[DetectedObject]:
    try:
        boxes = model.detect_objects(pil_image)
    except Exception as exc:
        logger.debug("media_analyzer: detect_objects failed: %s", exc)
        return []
    out: list[DetectedObject] = []
    seen: set[str] = set()
    for box in boxes:
        try:
            label = str(box.label or "").strip().lower()
            conf = float(box.confidence)
        except Exception:
            continue
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(DetectedObject(label=label, confidence=conf))
    return out


async def analyze_image_for_location(image_bytes: bytes) -> MediaAnalysis:
    """Run OCR + object detection on a single image. Either or both channels
    may silently no-op when their model isn't loadable."""
    if _DISABLED or not image_bytes:
        return MediaAnalysis()

    ocr_model, objdet_model = await asyncio.gather(_get_ocr(), _get_objdet())
    if ocr_model is None and objdet_model is None:
        return MediaAnalysis()

    pil_image = await asyncio.to_thread(_open_image_sync, image_bytes)
    if pil_image is None:
        return MediaAnalysis()

    async with _inference_semaphore:
        ocr_lines: list[OCRLine] = []
        objects: list[DetectedObject] = []
        if ocr_model is not None:
            ocr_lines = await asyncio.to_thread(_run_ocr_sync, ocr_model, pil_image)
        if objdet_model is not None:
            objects = await asyncio.to_thread(
                _run_objdet_sync, objdet_model, pil_image
            )

    return MediaAnalysis(ocr_lines=ocr_lines, objects=objects)
