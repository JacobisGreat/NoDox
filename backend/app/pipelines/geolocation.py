"""Geolocation pipeline.

Looks at every public post image and tries to figure out where it was
taken. Five signal channels feed an aggregator:

1. EXIF GPS — the highest-confidence channel, when present at all.
2. Instagram location tag — the user explicitly tagged the spot.
3. VLM analysis — Haiku scans the image for street signs, transit
   logos, license plates, vegetation, architectural style, etc.
4. GeoCLIP — a CLIP-based embedding model (vendored under
   ``systems/geo-clip``) that predicts GPS coords directly from pixels.
5. media-analyzer — vendored ``systems/media-analyzer`` package; runs
   ResNet-gated Tesseract OCR plus DETR object detection, geocodes any
   locatable text via the embedding-based geocoder, and surfaces
   locale-specific object labels as corroborating signal.

Per-image findings are emitted the moment they're discovered so the
dashboard streams in real time. Once enough signals accumulate, Sonnet
reconciles them into region-level clusters.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

from app.config import get_settings
from app.schemas.events import (
    cost_update as _cost_update_event,
    finding as _finding_event,
    pipeline_status as _pipeline_status_event,
)
from app.schemas.findings import Finding
from app.services.geocode_text import GeocodeHit, geocode_text
from app.services.media_analyzer_bridge import (
    MediaAnalysis,
    analyze_image_for_location as _media_analyzer_analyze,
)
from app.services.media_enrichment import reverse_geocode_coords
from app.session_store import SessionState

try:
    from PIL import Image
    from PIL.ExifTags import GPSTAGS
except ImportError:  # pragma: no cover — declared in requirements.txt
    Image = None  # type: ignore[assignment]
    GPSTAGS = {}  # type: ignore[assignment]


_PIPELINE = "geolocation"
_MAX_POSTS = 20
_MIN_SIGNALS_FOR_AGGREGATION = 3
_GEOCLIP_MIN_CONFIDENCE = 0.10
_GEOCLIP_FINDING_THRESHOLD = 0.20
_CLUSTER_EMIT_THRESHOLD = 0.40

# Signal categories. Image-based scanning is the primary evidence channel —
# the centroid and aggregator decision should be driven by these. Instagram
# tags are downgraded to corroboration only: they refine or verify an
# image-derived cluster but are not enough on their own to claim a region
# at high confidence. When image scanning yields nothing usable, tags are
# surfaced as an explicit "fallback estimate".
_CAT_IMAGE_PRIMARY = "image_primary"  # EXIF GPS, GeoCLIP top prediction
_CAT_IMAGE_SECONDARY = "image_secondary"  # VLM landmark, OCR text, objects
_CAT_TAG = "tag"  # Instagram location tag (corroboration, not primary)
_IMAGE_CATEGORIES = {_CAT_IMAGE_PRIMARY, _CAT_IMAGE_SECONDARY}

# Rough cost estimates used for budget-gating before each call.
_HAIKU_VISION_COST_GUESS = 0.01
_SONNET_AGGREGATION_COST_GUESS = 0.03


_VLM_SYSTEM_PROMPT = (
    "You are a geolocation analyst. Examine this image for location signals: "
    "street signs, business names, transit logos, license plate formats, "
    "landmarks, architectural style, vegetation type, language on signs, "
    "shadow direction. Respond with JSON: {\"signals\": [{\"type\": str, "
    "\"description\": str, \"location_hint\": str, \"confidence\": float}], "
    "\"best_guess_region\": str | null}"
)

_AGGREGATOR_SYSTEM_PROMPT = (
    "You are a geolocation intelligence aggregator. You will receive two "
    "groups of signals harvested from a target's recent posts:\n\n"
    "  IMAGE_DERIVED — primary evidence. EXIF GPS, GeoCLIP image-to-coord "
    "predictions, OCR text from photos, visual landmark recognitions, "
    "object detections. These come from analyzing the image content itself.\n"
    "  TAG_BASED — corroboration only. Instagram location tags the user "
    "(or someone) attached to the post. Tags are easy to spoof or apply to "
    "unrelated posts; they MUST NOT be the sole basis for a high-confidence "
    "cluster.\n\n"
    "Decision rules:\n"
    "1. Base every cluster's region on IMAGE_DERIVED evidence first.\n"
    "2. Use TAG_BASED signals only to corroborate, refine, or sanity-check "
    "an already image-supported region.\n"
    "3. If a cluster is supported only by TAG_BASED signals (no image "
    "evidence at all), label it with risk_level LOW and confidence ≤ 0.45 — "
    "it is a tag-only fallback estimate, not a confirmed location.\n"
    "4. If image evidence exists but contradicts the tags, prefer the image "
    "evidence and flag the inconsistency in the evidence list.\n\n"
    "Respond with JSON: {\"clusters\": [{\"region\": str, "
    "\"confidence\": float, \"evidence\": [str], "
    "\"risk_level\": \"LOW\"|\"MEDIUM\"|\"HIGH\"|\"CRITICAL\", "
    "\"primary_source\": \"image\"|\"tag_fallback\"}]}"
)


# ---------------------------------------------------------------------------
# Lazy GeoCLIP loader. Heavy import (torch + model weights) — we only pay
# the cost once per process and only when geolocation actually runs.
# ---------------------------------------------------------------------------

_geoclip_model: Any = None
_geoclip_load_attempted = False
_geoclip_load_error: str | None = None
_geoclip_lock = asyncio.Lock()
_geoclip_predict_lock: Any = None  # threading.Lock — created lazily


def _load_geoclip_sync() -> Any:
    """Synchronous loader. Always called under ``_geoclip_lock``.

    Picks CUDA when available unless ``NODOX_GEOCLIP_DEVICE=cpu`` forces CPU.
    """
    global _geoclip_model, _geoclip_load_attempted, _geoclip_load_error
    if _geoclip_model is not None or _geoclip_load_attempted:
        return _geoclip_model
    _geoclip_load_attempted = True
    try:
        from geoclip import GeoCLIP  # type: ignore[import-not-found]

        model = GeoCLIP()
        # GPU detection: opt-in via env, default to CUDA if available.
        device_pref = os.environ.get("NODOX_GEOCLIP_DEVICE", "").lower()
        if device_pref == "cpu":
            target = "cpu"
        else:
            try:
                import torch  # type: ignore[import-not-found]

                target = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                target = "cpu"
        if target != "cpu":
            try:
                model = model.to(target)
            except Exception:
                target = "cpu"
        _geoclip_model = model
        _geoclip_load_error = None
    except Exception as exc:  # noqa: BLE001 — any failure disables this channel
        _geoclip_load_error = str(exc)
        _geoclip_model = None
    return _geoclip_model


async def _get_geoclip_model() -> Any:
    """Async-safe lazy loader. Mirrors identity._get_sentence_model."""
    if _geoclip_model is not None or _geoclip_load_attempted:
        return _geoclip_model
    async with _geoclip_lock:
        if _geoclip_model is not None or _geoclip_load_attempted:
            return _geoclip_model
        return await asyncio.to_thread(_load_geoclip_sync)


async def warm_geoclip() -> None:
    """Pre-load geoclip during FastAPI startup so the first audit doesn't
    pay the 30-90s cold start. Safe to call multiple times — no-op if
    already loaded or previously failed.
    """
    await _get_geoclip_model()


# ---------------------------------------------------------------------------
# EXIF GPS helpers.
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            num, den = value.numerator, value.denominator  # type: ignore[attr-defined]
            if den == 0:
                return None
            return num / den
        except Exception:
            return None


def _dms_to_decimal(dms: Any) -> float | None:
    if dms is None:
        return None
    try:
        d, m, s = dms[0], dms[1], dms[2]
    except Exception:
        return None
    df = _to_float(d)
    mf = _to_float(m)
    sf = _to_float(s)
    if df is None or mf is None or sf is None:
        return None
    return df + mf / 60.0 + sf / 3600.0


def _extract_gps_sync(image_bytes: bytes) -> tuple[float, float] | None:
    if Image is None:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        exif = img._getexif()  # type: ignore[attr-defined]
    except Exception:
        return None
    if not exif:
        return None
    gps_info = exif.get(34853)  # GPSInfo IFD tag id
    if not gps_info:
        return None
    try:
        gps_data = {GPSTAGS.get(k, k): v for k, v in gps_info.items()}
    except Exception:
        return None
    lat = _dms_to_decimal(gps_data.get("GPSLatitude"))
    lon = _dms_to_decimal(gps_data.get("GPSLongitude"))
    if lat is None or lon is None:
        return None
    if str(gps_data.get("GPSLatitudeRef", "N")).upper().startswith("S"):
        lat = -lat
    if str(gps_data.get("GPSLongitudeRef", "E")).upper().startswith("W"):
        lon = -lon
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


# ---------------------------------------------------------------------------
# GeoCLIP helpers.
# ---------------------------------------------------------------------------


def _geoclip_predict_sync(image_bytes: bytes, top_k: int = 5) -> list[dict]:
    global _geoclip_predict_lock
    if _geoclip_predict_lock is None:
        import threading

        _geoclip_predict_lock = threading.Lock()
    model = _geoclip_model
    if model is None:
        return []
    tmp_path: str | None = None
    try:
        # GeoCLIP expects an image path on disk. Write to a temp file we
        # delete in the finally block so we never leak inodes.
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg", prefix="nodoxx_geo_")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(image_bytes)
        # Serialize torch forward passes to avoid state thrash under
        # concurrent post processing.
        with _geoclip_predict_lock:
            result = model.predict(tmp_path, top_k=top_k)
    except Exception:
        return []
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    predictions: list[dict] = []
    try:
        # geoclip returns (gps_tensor, prob_tensor) in newer versions.
        if isinstance(result, tuple) and len(result) == 2:
            coords, probs = result
            for i in range(min(top_k, len(coords))):
                lat = float(coords[i][0])
                lon = float(coords[i][1])
                conf = float(probs[i])
                predictions.append({"lat": lat, "lon": lon, "confidence": conf})
        elif isinstance(result, list):
            for entry in result[:top_k]:
                if isinstance(entry, (list, tuple)) and len(entry) >= 3:
                    predictions.append(
                        {
                            "lat": float(entry[0]),
                            "lon": float(entry[1]),
                            "confidence": float(entry[2]),
                        }
                    )
    except Exception:
        return []
    return predictions


# ---------------------------------------------------------------------------
# JSON parsing helper for model responses.
# ---------------------------------------------------------------------------


def _parse_json_response(raw: str) -> dict | None:
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# ---------------------------------------------------------------------------
# Finding emission helpers.
# ---------------------------------------------------------------------------


async def _emit_finding(session: SessionState, finding: Finding) -> None:
    payload = finding.to_dict()
    findings_log: list[dict] = session.data.setdefault("findings", [])
    tagged = dict(payload)
    tagged["_pipeline"] = _PIPELINE
    findings_log.append(tagged)
    await session.publish_event(_finding_event(_PIPELINE, payload))


async def _geocode_first(*texts: str | None) -> GeocodeHit | None:
    """Return the first resolved geocode hit across ``texts``, in order."""
    for raw in texts:
        if not raw:
            continue
        hit = await geocode_text(raw)
        if hit is not None:
            return hit
    return None


def _attach_geocode_metadata(metadata: dict[str, Any], hit: GeocodeHit) -> None:
    """Attach lat/lon (and city/country if absent) to a finding's metadata."""
    metadata.setdefault("lat", hit.lat)
    metadata.setdefault("lon", hit.lon)
    if hit.country and "country" not in metadata:
        metadata["country"] = hit.country
    if hit.kind == "city" and "city" not in metadata:
        metadata["city"] = hit.name
    metadata.setdefault("geocoded_via", hit.method)
    metadata.setdefault("geocoded_match", hit.name)
    metadata.setdefault("geocode_confidence", hit.confidence)


async def _emit_cost(session: SessionState) -> None:
    tracker = session.data.get("cost_tracker")
    if tracker is None:
        return
    await session.publish_event(_cost_update_event(tracker.total_usd))


def _shortcode_url(shortcode: str) -> str:
    return f"https://instagram.com/p/{shortcode}"


# ---------------------------------------------------------------------------
# Per-post processing.
# ---------------------------------------------------------------------------


async def _process_location_tag(session: SessionState, post: dict) -> dict | None:
    location_name = post.get("location_name")
    if not location_name:
        return None
    shortcode = post.get("shortcode") or "unknown"
    metadata: dict[str, Any] = {
        "location_name": location_name,
        "location_id": post.get("location_id"),
        "shortcode": shortcode,
        "signal_category": _CAT_TAG,
    }
    hit = await _geocode_first(location_name)
    if hit is not None:
        _attach_geocode_metadata(metadata, hit)
    finding = Finding(
        source="instagram_location_tag",
        evidence_chain=[
            f"Post: {_shortcode_url(shortcode)}",
            f"Instagram location tag: {location_name}",
        ]
        + (
            [f"Geocoded to: {hit.name}, {hit.country} ({hit.lat:.4f}, {hit.lon:.4f})"]
            if hit is not None
            else []
        ),
        confidence=0.85,
        risk_level="HIGH",
        remediation=(
            "Open Instagram, edit the post at "
            f"{_shortcode_url(shortcode)}, and remove the tagged location. "
            "Going forward, disable location tagging in Settings > Privacy."
        ),
        metadata=metadata,
    )
    await _emit_finding(session, finding)
    signal: dict[str, Any] = {
        "type": "instagram_location_tag",
        "category": _CAT_TAG,
        "description": location_name,
        "location_hint": location_name,
        "confidence": 0.85,
        "shortcode": shortcode,
    }
    if hit is not None:
        signal["lat"] = hit.lat
        signal["lon"] = hit.lon
    return signal


async def _process_vlm(
    session: SessionState,
    post: dict,
    image_bytes: bytes,
    media_type: str,
) -> list[dict]:
    settings = get_settings()
    cost_tracker = session.data.get("cost_tracker")
    anthropic = session.data.get("anthropic")
    sem: asyncio.Semaphore | None = session.data.get("vision_semaphore")

    if anthropic is None or not settings.gemini_api_key:
        return []
    if cost_tracker is not None and not cost_tracker.can_spend(
        _HAIKU_VISION_COST_GUESS
    ):
        return []

    shortcode = post.get("shortcode") or "unknown"

    async def _call() -> tuple[str, dict] | None:
        try:
            return await anthropic.call_vision(
                model=settings.gemini_fast_model,
                system=_VLM_SYSTEM_PROMPT,
                image_bytes=image_bytes,
                image_media_type=media_type,
                prompt=(
                    f"Analyze this Instagram post image (shortcode {shortcode}) "
                    "for any location signals. Return JSON only."
                ),
                max_tokens=1024,
            )
        except Exception:
            return None

    if sem is not None:
        async with sem:
            result = await _call()
    else:
        result = await _call()

    if result is None:
        return []
    text, usage = result
    if cost_tracker is not None:
        cost_tracker.record_anthropic(usage, scope="geolocation")
        await _emit_cost(session)

    parsed = _parse_json_response(text)
    if parsed is None:
        return []

    raw_signals = parsed.get("signals") or []
    best_guess = parsed.get("best_guess_region")
    signals: list[dict] = []
    if isinstance(raw_signals, list):
        for sig in raw_signals:
            if not isinstance(sig, dict):
                continue
            try:
                conf = float(sig.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            signals.append(
                {
                    "type": str(sig.get("type", "") or "vlm_signal"),
                    "category": _CAT_IMAGE_SECONDARY,
                    "description": str(sig.get("description", "") or ""),
                    "location_hint": str(sig.get("location_hint", "") or ""),
                    "confidence": max(0.0, min(1.0, conf)),
                    "best_guess_region": str(best_guess) if best_guess else None,
                    "shortcode": shortcode,
                }
            )

    # Per-post emission for distinctive landmark hits — corroboration is
    # the aggregator's job, but a confident landmark is worth surfacing now.
    for sig in signals:
        if sig["confidence"] >= 0.75 and sig["location_hint"]:
            metadata: dict[str, Any] = {
                "shortcode": shortcode,
                "signal_type": sig["type"],
                "location_hint": sig["location_hint"],
                "signal_category": _CAT_IMAGE_SECONDARY,
            }
            hit = await _geocode_first(
                sig["location_hint"], sig.get("best_guess_region")
            )
            if hit is not None:
                _attach_geocode_metadata(metadata, hit)
                sig["lat"] = hit.lat
                sig["lon"] = hit.lon
            evidence = [
                f"Post: {_shortcode_url(shortcode)}",
                f"Visual signal: {sig['type']}",
                f"Description: {sig['description']}",
                f"Hint: {sig['location_hint']}",
            ]
            if hit is not None:
                evidence.append(
                    f"Geocoded to: {hit.name}, {hit.country} "
                    f"({hit.lat:.4f}, {hit.lon:.4f})"
                )
            finding = Finding(
                source="vlm_landmark",
                evidence_chain=evidence,
                confidence=min(0.8, sig["confidence"]),
                risk_level="MEDIUM",
                remediation=(
                    f"Review the post at {_shortcode_url(shortcode)} — the "
                    f"image visibly reveals {sig['location_hint']}. Crop or "
                    "remove identifying scenery, or delete the post."
                ),
                metadata=metadata,
            )
            await _emit_finding(session, finding)

    return signals


async def _process_geoclip(
    session: SessionState, post: dict, image_bytes: bytes
) -> list[dict]:
    if await _get_geoclip_model() is None:
        return []
    predictions = await asyncio.to_thread(_geoclip_predict_sync, image_bytes, 5)
    if not predictions:
        return []
    shortcode = post.get("shortcode") or "unknown"
    relevant: list[dict] = []
    top_emitted = False
    for idx, pred in enumerate(predictions):
        conf = float(pred.get("confidence", 0.0))
        if conf < _GEOCLIP_MIN_CONFIDENCE:
            continue
        relevant.append(
            {
                "type": "geoclip_prediction",
                "category": _CAT_IMAGE_PRIMARY,
                "description": (
                    f"GeoCLIP top prediction at "
                    f"({pred['lat']:.4f}, {pred['lon']:.4f})"
                ),
                "location_hint": f"{pred['lat']:.4f}, {pred['lon']:.4f}",
                "confidence": conf,
                "shortcode": shortcode,
                "lat": pred["lat"],
                "lon": pred["lon"],
            }
        )
        # Emit a per-post finding for the top prediction once it clears the
        # finding threshold — the map needs lat/lon-bearing findings to plot.
        if not top_emitted and idx == 0 and conf >= _GEOCLIP_FINDING_THRESHOLD:
            top_emitted = True
            place = await asyncio.to_thread(
                reverse_geocode_coords, pred["lat"], pred["lon"]
            )
            place_label = place.display() if place else None
            evidence = [
                f"Post: {_shortcode_url(shortcode)}",
                "GeoCLIP image-to-coordinate prediction",
                f"Predicted coordinates: {pred['lat']:.4f}, {pred['lon']:.4f}",
            ]
            if place_label:
                evidence.append(f"Reverse-geocoded location: {place_label}")
            metadata: dict[str, Any] = {
                "lat": pred["lat"],
                "lon": pred["lon"],
                "shortcode": shortcode,
                "model_confidence": conf,
                "signal_category": _CAT_IMAGE_PRIMARY,
            }
            if place is not None:
                metadata.update(
                    city=place.city,
                    country=place.country,
                    country_code=place.country_code,
                )
            risk_level = "MEDIUM" if conf >= 0.40 else "LOW"
            finding = Finding(
                source="geoclip_prediction",
                evidence_chain=evidence,
                confidence=min(0.85, conf),
                risk_level=risk_level,  # type: ignore[arg-type]
                remediation=(
                    "GeoCLIP inferred this post's likely region from visual "
                    "cues alone. If the prediction is accurate, crop or remove "
                    f"distinctive scenery in {_shortcode_url(shortcode)}."
                ),
                metadata=metadata,
            )
            await _emit_finding(session, finding)
    return relevant


# Object labels that suggest locale-specific signal. Used to classify
# whether a DETR detection is worth surfacing as evidence.
_LOCATION_HINT_OBJECTS: set[str] = {
    "bus",
    "train",
    "tram",
    "subway",
    "taxi",
    "license plate",
    "stop sign",
    "traffic light",
    "fire hydrant",
    "telephone booth",
    "phone booth",
    "double-decker bus",
}


async def _process_media_analyzer(
    session: SessionState, post: dict, image_bytes: bytes
) -> list[dict]:
    """Run media-analyzer (OCR + DETR object detection) on the image. For
    every OCR line and recognized object that resolves to a known
    place/landmark via the geocoder, emit a finding and contribute a
    signal so the aggregator can corroborate."""
    analysis: MediaAnalysis = await _media_analyzer_analyze(image_bytes)
    if analysis.is_empty:
        return []

    shortcode = post.get("shortcode") or "unknown"
    signals: list[dict] = []

    # ---- OCR-based geocoding -----------------------------------------------
    seen_geocoded: set[str] = set()
    for line in analysis.ocr_lines:
        text = line.text.strip()
        if len(text) < 3:
            continue
        hit = await geocode_text(text)
        if hit is None:
            continue
        key = f"{hit.lat:.4f},{hit.lon:.4f}"
        if key in seen_geocoded:
            continue
        seen_geocoded.add(key)

        finding_metadata: dict[str, Any] = {
            "shortcode": shortcode,
            "ocr_text": text,
            "ocr_confidence": line.confidence,
            "lat": hit.lat,
            "lon": hit.lon,
            "geocoded_via": hit.method,
            "geocoded_match": hit.name,
            "geocode_confidence": hit.confidence,
            "signal_category": _CAT_IMAGE_SECONDARY,
        }
        if hit.country:
            finding_metadata["country"] = hit.country
        if hit.kind == "city":
            finding_metadata["city"] = hit.name

        await _emit_finding(
            session,
            Finding(
                source="media_analyzer_ocr",
                evidence_chain=[
                    f"Post: {_shortcode_url(shortcode)}",
                    f"OCR text in image: {text!r} (conf {line.confidence:.2f})",
                    f"Geocoded to: {hit.name}, {hit.country} "
                    f"({hit.lat:.4f}, {hit.lon:.4f})",
                ],
                confidence=min(0.85, 0.5 * line.confidence + 0.5 * hit.confidence),
                risk_level="MEDIUM",
                remediation=(
                    f"Text visible in {_shortcode_url(shortcode)} "
                    f"(\"{text}\") reveals {hit.name}. Crop the post or remove "
                    "frames that show readable signage / text."
                ),
                metadata=finding_metadata,
            ),
        )
        signals.append(
            {
                "type": "media_analyzer_ocr",
                "category": _CAT_IMAGE_SECONDARY,
                "description": f"OCR matched {hit.name}",
                "location_hint": f"{hit.name}, {hit.country}".strip(", "),
                "confidence": min(0.85, 0.5 * line.confidence + 0.5 * hit.confidence),
                "shortcode": shortcode,
                "lat": hit.lat,
                "lon": hit.lon,
            }
        )

    # ---- Object-detection signals -----------------------------------------
    # Object labels alone don't give coordinates, but they corroborate the
    # aggregator's region call (e.g. "double-decker bus" + UK signals).
    for obj in analysis.objects:
        if obj.label not in _LOCATION_HINT_OBJECTS:
            continue
        signals.append(
            {
                "type": "media_analyzer_object",
                "category": _CAT_IMAGE_SECONDARY,
                "description": f"Detected {obj.label} in image",
                "location_hint": obj.label,
                "confidence": min(0.6, obj.confidence),
                "shortcode": shortcode,
            }
        )

    # Surface OCR text that didn't geocode but might still be useful evidence,
    # so the aggregator's Sonnet pass can reason over it.
    for line in analysis.ocr_lines:
        text = line.text.strip()
        if not text or any(
            text.lower() in s.get("location_hint", "").lower() for s in signals
        ):
            continue
        signals.append(
            {
                "type": "media_analyzer_ocr_raw",
                "category": _CAT_IMAGE_SECONDARY,
                "description": text[:120],
                "location_hint": text[:120],
                "confidence": min(0.5, line.confidence),
                "shortcode": shortcode,
            }
        )

    return signals


async def _process_post(
    session: SessionState, post: dict
) -> tuple[list[dict], bool]:
    """Run every channel for one post.

    Returns ``(signals, image_downloaded)`` so the caller can track how
    many post images actually came down — Instagram's CDN often blocks
    direct fetches and silently dropping every post would otherwise be
    indistinguishable from "this user posts no location-bearing content".

    Channel ordering is deliberate: the IG location tag lives in post
    metadata and runs first regardless of whether the image is fetchable.
    Image-bytes channels (EXIF/VLM/GeoCLIP/media-analyzer) only run when
    the download succeeded.
    """
    signals: list[dict] = []

    # Channel 2 first — Instagram location tag (no image bytes needed).
    tag_signal = await _process_location_tag(session, post)
    if tag_signal is not None:
        signals.append(tag_signal)

    image_url = post.get("image_url")
    if not image_url:
        return signals, False

    downloader = session.data.get("image_downloader")
    if downloader is None:
        return signals, False

    try:
        image_bytes, media_type = await downloader.download(image_url)
    except Exception as exc:
        logger.debug(
            "image download failed for post %s: %s",
            post.get("shortcode") or "?",
            exc,
        )
        return signals, False

    # Channel 1 — EXIF GPS. If present, also feed the aggregator.
    coords = await asyncio.to_thread(_extract_gps_sync, image_bytes)
    if coords is not None:
        lat, lon = coords
        shortcode = post.get("shortcode") or "unknown"
        place = await asyncio.to_thread(reverse_geocode_coords, lat, lon)
        place_label = place.display() if place else None
        evidence = [
            f"Post: {_shortcode_url(shortcode)}",
            "EXIF GPSInfo present in uploaded image",
            f"Decoded coordinates: {lat:.6f}, {lon:.6f}",
        ]
        if place_label:
            evidence.append(f"Reverse-geocoded location: {place_label}")
        finding_metadata: dict[str, Any] = {
            "lat": lat,
            "lon": lon,
            "shortcode": shortcode,
            "signal_category": _CAT_IMAGE_PRIMARY,
        }
        if place is not None:
            finding_metadata.update(
                city=place.city,
                country=place.country,
                country_code=place.country_code,
            )
        finding = Finding(
            source="exif_gps",
            evidence_chain=evidence,
            confidence=0.95,
            risk_level="CRITICAL",
            remediation=(
                "Strip EXIF data before posting. Go to Instagram Settings > "
                "Privacy > remove location data from posts. Delete and "
                f"re-upload post {_shortcode_url(shortcode)}"
            ),
            metadata=finding_metadata,
        )
        await _emit_finding(session, finding)
        signals.append(
            {
                "type": "exif_gps",
                "category": _CAT_IMAGE_PRIMARY,
                "description": "EXIF GPS coordinates",
                "location_hint": place_label or f"{lat:.4f}, {lon:.4f}",
                "confidence": 0.95,
                "shortcode": shortcode,
                "lat": lat,
                "lon": lon,
                "city": place.city if place else None,
                "country": place.country if place else None,
            }
        )

    # Channel 3 — VLM analysis.
    vlm_signals = await _process_vlm(session, post, image_bytes, media_type)
    signals.extend(vlm_signals)

    # Channel 4 — GeoCLIP (image → coordinate prediction).
    geoclip_signals = await _process_geoclip(session, post, image_bytes)
    signals.extend(geoclip_signals)

    # Channel 5 — media-analyzer (OCR + object detection from systems/).
    try:
        media_signals = await _process_media_analyzer(session, post, image_bytes)
    except Exception as exc:  # noqa: BLE001 — never let one bad image stop the pipeline
        logger.debug("media_analyzer channel failed for post: %s", exc)
        media_signals = []
    signals.extend(media_signals)

    return signals, True


# ---------------------------------------------------------------------------
# Aggregation pass.
# ---------------------------------------------------------------------------


async def _run_aggregation(
    session: SessionState,
    image_signals: list[dict],
    tag_signals: list[dict],
    *,
    primary_source: str,
) -> None:
    """Aggregate partitioned signals into region clusters.

    ``primary_source`` is ``"image"`` when at least one image-derived
    signal exists, or ``"tag_fallback"`` when only Instagram tags survived
    image scanning. The prompt makes the distinction explicit so Sonnet
    weights image-derived evidence higher than tags.
    """
    settings = get_settings()
    cost_tracker = session.data.get("cost_tracker")
    anthropic = session.data.get("anthropic")
    if anthropic is None or not settings.gemini_api_key:
        return
    if cost_tracker is not None and not cost_tracker.can_spend(
        _SONNET_AGGREGATION_COST_GUESS
    ):
        return

    if primary_source == "tag_fallback":
        user_prompt = (
            "IMAGE_DERIVED signals: NONE — image-based scanners (EXIF, "
            "GeoCLIP, OCR, VLM, object detection) produced no usable "
            "location evidence for this target's posts.\n\n"
            "TAG_BASED signals (fallback only — flag clusters as "
            "tag_fallback with confidence ≤ 0.45):\n"
            + json.dumps(tag_signals, indent=2, ensure_ascii=False)
        )
    else:
        user_prompt = (
            "IMAGE_DERIVED signals (primary evidence — base your cluster "
            "decisions on these):\n"
            + json.dumps(image_signals, indent=2, ensure_ascii=False)
            + "\n\nTAG_BASED signals (corroboration only — use to verify "
            "or refine the image-derived clusters; ignore if they conflict "
            "without image support):\n"
            + json.dumps(tag_signals, indent=2, ensure_ascii=False)
        )

    try:
        text, usage = await anthropic.call_text(
            model=settings.gemini_pro_model,
            system=_AGGREGATOR_SYSTEM_PROMPT,
            user_content=user_prompt,
            max_tokens=1500,
            response_json=True,
        )
    except Exception:
        return

    if cost_tracker is not None:
        cost_tracker.record_anthropic(usage, scope="geolocation")
        await _emit_cost(session)

    parsed = _parse_json_response(text)
    if parsed is None:
        return

    clusters = parsed.get("clusters") or []
    if not isinstance(clusters, list):
        return

    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        try:
            confidence = float(cluster.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < _CLUSTER_EMIT_THRESHOLD:
            continue
        region = str(cluster.get("region", "") or "").strip()
        if not region:
            continue
        evidence_raw = cluster.get("evidence") or []
        evidence_lines = [str(e) for e in evidence_raw if e]
        risk_level_raw = str(cluster.get("risk_level", "MEDIUM") or "MEDIUM").upper()
        if risk_level_raw not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            risk_level_raw = "MEDIUM"

        evidence_chain = [
            f"Aggregated region: {region}",
            f"Cluster confidence: {confidence:.2f}",
        ] + [f"Evidence: {line}" for line in evidence_lines]

        cluster_primary = str(
            cluster.get("primary_source", primary_source) or primary_source
        ).strip().lower()
        if cluster_primary not in {"image", "tag_fallback"}:
            cluster_primary = primary_source

        metadata: dict[str, Any] = {
            "region": region,
            "evidence": evidence_lines,
            "cluster_confidence": confidence,
            "primary_source": cluster_primary,
            # Aggregator findings inherit the strongest contributing
            # category. Tag-fallback clusters carry the tag category so
            # the frontend can de-emphasize them on the map.
            "signal_category": (
                _CAT_IMAGE_PRIMARY
                if cluster_primary == "image"
                else _CAT_TAG
            ),
        }
        # Geocode the region (and fall back to scanning evidence lines) so the
        # cluster lands on the map.
        hit = await _geocode_first(region, *evidence_lines)
        if hit is not None:
            _attach_geocode_metadata(metadata, hit)
            evidence_chain.append(
                f"Geocoded to: {hit.name}, {hit.country} "
                f"({hit.lat:.4f}, {hit.lon:.4f})"
            )
        if cluster_primary == "tag_fallback":
            evidence_chain.append(
                "Source: Instagram location tags only (image-based "
                "scanning produced no signal for this region)."
            )

        finding = Finding(
            source="geo_aggregation",
            evidence_chain=evidence_chain,
            confidence=max(0.0, min(1.0, confidence)),
            risk_level=risk_level_raw,  # type: ignore[arg-type]
            remediation=(
                f"Multiple posts cluster around {region}. Audit recent posts "
                "for tagged locations, recognizable backgrounds, and EXIF "
                "metadata; remove or crop content that pinpoints this area."
            ),
            metadata=metadata,
        )
        await _emit_finding(session, finding)


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def _categorize_signals(
    all_signals: list[dict],
) -> tuple[list[dict], list[dict], dict[str, int]]:
    """Partition signals into (image, tag) lists and tally per-channel
    counts for the scan summary."""
    image_signals: list[dict] = []
    tag_signals: list[dict] = []
    counts: dict[str, int] = {
        "exif_gps": 0,
        "geoclip": 0,
        "vlm": 0,
        "ocr_geocoded": 0,
        "ocr_raw": 0,
        "objects": 0,
        "tag": 0,
    }
    for sig in all_signals:
        category = sig.get("category", _CAT_IMAGE_SECONDARY)
        sig_type = sig.get("type", "")
        if category == _CAT_TAG:
            tag_signals.append(sig)
            counts["tag"] += 1
        else:
            image_signals.append(sig)
        if sig_type == "exif_gps":
            counts["exif_gps"] += 1
        elif sig_type == "geoclip_prediction":
            counts["geoclip"] += 1
        elif sig_type.startswith("vlm") or sig_type in {
            "vlm_signal",
            "street_sign",
            "landmark",
        }:
            counts["vlm"] += 1
        elif sig_type == "media_analyzer_ocr":
            counts["ocr_geocoded"] += 1
        elif sig_type == "media_analyzer_ocr_raw":
            counts["ocr_raw"] += 1
        elif sig_type == "media_analyzer_object":
            counts["objects"] += 1
    return image_signals, tag_signals, counts


async def run(session: SessionState) -> None:
    await session.publish_event(_pipeline_status_event(_PIPELINE, "running"))

    try:
        posts: list[dict] = list(session.data.get("posts") or [])
        video_count = sum(1 for p in posts if p.get("is_video"))
        image_posts = [p for p in posts if not p.get("is_video")]
        image_posts = image_posts[:_MAX_POSTS]

        # Diagnostic: surface how many posts are actually scannable. Without
        # this, an empty post list (Meta has been trimming the inline post
        # payload on anonymous web_profile_info calls) silently produces
        # zero findings and the pipeline reads as "instantly complete".
        await session.publish_event(
            _pipeline_status_event(
                _PIPELINE,
                "running",
                (
                    f"{len(posts)} post(s) loaded, "
                    f"{video_count} video(s) skipped, "
                    f"{len(image_posts)} image(s) queued for scanning."
                ),
            )
        )

        if not image_posts:
            await session.publish_event(
                _pipeline_status_event(
                    _PIPELINE,
                    "complete",
                    (
                        "No public image posts available to scan. Instagram's "
                        "anonymous endpoint may have returned 0 posts for this "
                        "profile, or all posts are videos."
                    ),
                )
            )
            return

        cost_tracker = session.data.get("cost_tracker")
        all_signals: list[dict] = []
        budget_exceeded = False
        downloads_attempted = 0
        downloads_ok = 0

        for post in image_posts:
            if cost_tracker is not None and not cost_tracker.can_spend(
                _HAIKU_VISION_COST_GUESS
            ):
                budget_exceeded = True
                break
            try:
                signals, image_ok = await _process_post(session, post)
            except Exception:
                # One bad post should never abort the pipeline.
                continue
            downloads_attempted += 1
            if image_ok:
                downloads_ok += 1
            all_signals.extend(signals)

        # Surface CDN download outcome to the dashboard. Without this the
        # user can't tell "no location signals because the account doesn't
        # post locations" from "no location signals because Instagram CDN
        # 403'd every download" — the latter is recoverable, the former
        # isn't.
        if downloads_attempted > 0 and downloads_ok < downloads_attempted:
            await session.publish_event(
                _pipeline_status_event(
                    _PIPELINE,
                    "running",
                    (
                        f"Downloaded {downloads_ok}/{downloads_attempted} post "
                        "images (Instagram CDN may be blocking direct fetches)."
                    ),
                )
            )

        if budget_exceeded:
            await session.publish_event(
                _pipeline_status_event(
                    _PIPELINE,
                    "budget_exceeded",
                    "Geolocation pipeline halted before all posts processed.",
                )
            )
            return

        # Partition by category and decide aggregation strategy.
        image_signals, tag_signals, counts = _categorize_signals(all_signals)
        has_image_signal = bool(image_signals)
        has_tag_signal = bool(tag_signals)

        # Aggregation routing:
        # 1. Image signals exist  → image-primary, tags corroborate
        # 2. No image, only tags → tag-fallback estimate (low confidence)
        # 3. Neither             → skip aggregation entirely
        total_signal_count = len(image_signals) + len(tag_signals)
        if has_image_signal and total_signal_count >= _MIN_SIGNALS_FOR_AGGREGATION:
            await _run_aggregation(
                session,
                image_signals,
                tag_signals,
                primary_source="image",
            )
        elif has_tag_signal and not has_image_signal:
            # Tag-only fallback: emit cluster guesses but flagged as low-confidence.
            await _run_aggregation(
                session,
                [],
                tag_signals,
                primary_source="tag_fallback",
            )

        await session.publish_event(_pipeline_status_event(_PIPELINE, "complete"))
    except Exception as exc:  # noqa: BLE001 — defensive top-level guard
        await session.publish_event(
            _pipeline_status_event(_PIPELINE, "error", f"{type(exc).__name__}: {exc}")
        )
