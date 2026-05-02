from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import mimetypes
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core.audit_context import ANTHROPIC_SEMAPHORE
from app.core.config import Settings
from app.core.session_store import EphemeralSessionStore, SessionState

logger = logging.getLogger(__name__)

PIPELINE_NAME = "geolocation"
MAX_MEDIA_POSTS = 50
ANTHROPIC_VISION_SEMAPHORE = ANTHROPIC_SEMAPHORE
POST_ANALYSIS_SEMAPHORE = asyncio.Semaphore(8)
GEOCLIP_MODEL_LOCK = asyncio.Lock()
_GEOCLIP_MODEL: Any | None = None

VISION_SYSTEM_PROMPT = """You are analyzing a photo for visible location signals. Return JSON only. No preamble.
Schema: {"signals": [{"type": string, "description": string, "region": string, "confidence": "low"|"medium"|"high"}]}
Types to look for: street_sign, business_name, transit_logo, license_plate_format, landmark, architectural_style, vegetation_climate, language_script, shadow_direction
Only report what is visibly present. Do not guess. If nothing identifiable is visible, return {"signals": []}."""


class GeolocationPipelineError(Exception):
    pass


class BudgetExceededError(GeolocationPipelineError):
    pass


@dataclass(slots=True)
class MediaPost:
    post_id: str
    media_url: str
    caption: str | None = None
    timestamp: str | None = None
    location: str | None = None


@dataclass(slots=True)
class GeoClipPrediction:
    lat: float
    lon: float
    probability: float


@dataclass(slots=True)
class VlmSignal:
    type: str
    description: str
    region: str
    confidence: str


@dataclass(slots=True)
class PostAnalysis:
    post: MediaPost
    geoclip_predictions: list[GeoClipPrediction] = field(default_factory=list)
    vlm_signals: list[VlmSignal] = field(default_factory=list)
    exif_coordinates: tuple[float, float] | None = None


@dataclass(slots=True)
class GeoClipCluster:
    centroid_lat: float
    centroid_lon: float
    post_ids: list[str] = field(default_factory=list)
    posts: list[dict[str, Any]] = field(default_factory=list)
    probabilities: list[float] = field(default_factory=list)
    max_radius_km: float = 0.0


def _session_total_cost(session: SessionState) -> float:
    if "cost_usd_total" in session.data:
        return float(session.data.get("cost_usd_total", 0.0))
    return float(session.data.get("audit_cost", 0.0))


def _token_from_nested(data: dict[str, Any], *path: str) -> str | None:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, str) and current.strip():
        return current.strip()
    return None


def _extract_access_token(session: SessionState) -> str | None:
    candidates = (
        ("meta_access_token",),
        ("instagram_access_token",),
        ("access_token",),
        ("auth", "access_token"),
        ("oauth", "access_token"),
        ("meta", "access_token"),
        ("meta_oauth", "access_token"),
        ("instagram", "access_token"),
        ("tokens", "meta"),
        ("tokens", "instagram"),
    )
    for path in candidates:
        token = _token_from_nested(session.data, *path)
        if token:
            return token
    return None


def _extract_cached_media(session: SessionState) -> list[dict[str, Any]]:
    for key in ("media", "me_media", "recent_media", "instagram_media"):
        value = session.data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_location(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, dict):
        parts = [
            value.get("name"),
            value.get("city"),
            value.get("state"),
            value.get("country"),
        ]
        text = ", ".join(str(part).strip() for part in parts if part)
        return text or None
    return None


def _normalize_media_items(items: list[dict[str, Any]]) -> list[MediaPost]:
    posts: list[MediaPost] = []
    for item in items:
        post_id = str(item.get("id") or "").strip()
        media_url = str(item.get("media_url") or item.get("thumbnail_url") or "").strip()
        if not post_id or not media_url:
            continue
        posts.append(
            MediaPost(
                post_id=post_id,
                media_url=media_url,
                caption=str(item.get("caption")).strip() if item.get("caption") else None,
                timestamp=str(item.get("timestamp")).strip() if item.get("timestamp") else None,
                location=_normalize_location(item.get("location")),
            )
        )
        if len(posts) >= MAX_MEDIA_POSTS:
            break
    return posts


async def _publish_pipeline_status(
    session: SessionState,
    store: EphemeralSessionStore,
    status: str,
    **payload: Any,
) -> None:
    async with session.state_lock:
        pipelines = session.data.setdefault("pipelines", {})
        geolocation_state = pipelines.setdefault(PIPELINE_NAME, {})
        geolocation_state["status"] = status
        geolocation_state.update(payload)
    await store.save(session)
    await session.publish_event(
        {
            "pipeline": PIPELINE_NAME,
            "pipeline_status": status,
            **payload,
        }
    )


async def _publish_finding(
    session: SessionState,
    store: EphemeralSessionStore,
    finding: dict[str, Any],
) -> None:
    async with session.state_lock:
        findings = session.data.setdefault("findings", [])
        findings.append(finding)
    await store.save(session)
    await session.publish_event(finding)


def _pricing_for_model(model: str, settings: Settings) -> tuple[float, float]:
    if model == settings.anthropic_haiku_model:
        return (
            settings.claude_haiku_input_cost_per_million,
            settings.claude_haiku_output_cost_per_million,
        )
    return (
        settings.claude_sonnet_input_cost_per_million,
        settings.claude_sonnet_output_cost_per_million,
    )


async def _record_anthropic_cost(
    session: SessionState,
    store: EphemeralSessionStore,
    model: str,
    usage: dict[str, Any] | None,
    settings: Settings,
) -> float:
    usage = usage or {}
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    input_rate, output_rate = _pricing_for_model(model, settings)
    delta = ((input_tokens * input_rate) + (output_tokens * output_rate)) / 1_000_000

    budget_crossed = False
    async with session.state_lock:
        total = _session_total_cost(session) + delta
        session.data["cost_usd_total"] = round(total, 6)
        session.data["audit_cost"] = round(total, 6)
        anthropic_usage = session.data.setdefault("anthropic_usage", {})
        model_usage = anthropic_usage.setdefault(
            model,
            {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
        model_usage["input_tokens"] += input_tokens
        model_usage["output_tokens"] += output_tokens
        model_usage["cost_usd"] = round(float(model_usage["cost_usd"]) + delta, 6)

        pipeline_state = session.data.setdefault("pipelines", {}).setdefault(PIPELINE_NAME, {})
        if total >= settings.audit_cost_ceiling_usd and not pipeline_state.get("budget_exceeded_emitted"):
            pipeline_state["budget_exceeded_emitted"] = True
            budget_crossed = True

    await store.save(session)
    if budget_crossed:
        await _publish_pipeline_status(
            session,
            store,
            "budget_exceeded",
            cost_usd_total=round(_session_total_cost(session), 6),
            budget_usd=settings.audit_cost_ceiling_usd,
        )
    return _session_total_cost(session)


async def _guard_budget(session: SessionState, settings: Settings) -> None:
    if _session_total_cost(session) >= settings.audit_cost_ceiling_usd:
        raise BudgetExceededError("Anthropic audit budget exhausted.")


def _parse_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        try:
            return float(stripped)
        except ValueError:
            match = re.match(r"^(-?\d+(?:\.\d+)?)", stripped)
            if match:
                return float(match.group(1))
    return None


def _extract_exif_gps_sync(image_path: str) -> tuple[float, float] | None:
    import exiftool

    with exiftool.ExifToolHelper() as helper:
        metadata_items = helper.get_metadata(image_path)

    if not metadata_items:
        return None
    metadata = metadata_items[0]
    lat = (
        _parse_float(metadata.get("Composite:GPSLatitude"))
        or _parse_float(metadata.get("EXIF:GPSLatitude"))
        or _parse_float(metadata.get("XMP:GPSLatitude"))
    )
    lon = (
        _parse_float(metadata.get("Composite:GPSLongitude"))
        or _parse_float(metadata.get("EXIF:GPSLongitude"))
        or _parse_float(metadata.get("XMP:GPSLongitude"))
    )
    if lat is None or lon is None:
        return None
    return (lat, lon)


async def _extract_exif_gps(image_path: str) -> tuple[float, float] | None:
    try:
        return await asyncio.to_thread(_extract_exif_gps_sync, image_path)
    except FileNotFoundError:
        logger.warning("exiftool binary not found; skipping EXIF GPS check")
    except Exception:
        logger.exception("EXIF GPS extraction failed for %s", image_path)
    return None


async def _get_geoclip_model() -> Any:
    global _GEOCLIP_MODEL
    if _GEOCLIP_MODEL is not None:
        return _GEOCLIP_MODEL

    async with GEOCLIP_MODEL_LOCK:
        if _GEOCLIP_MODEL is None:
            from geoclip import GeoCLIP

            _GEOCLIP_MODEL = await asyncio.to_thread(GeoCLIP)
    return _GEOCLIP_MODEL


async def _run_geoclip(image_path: str) -> list[GeoClipPrediction]:
    try:
        model = await _get_geoclip_model()
        gps_predictions, probabilities = await asyncio.to_thread(model.predict, image_path, 5)
    except Exception:
        logger.exception("GeoCLIP inference failed for %s", image_path)
        return []

    predictions: list[GeoClipPrediction] = []
    for coords, probability in zip(gps_predictions, probabilities):
        try:
            lat = float(coords[0])
            lon = float(coords[1])
            prob = float(probability)
        except (TypeError, ValueError, IndexError):
            continue
        predictions.append(GeoClipPrediction(lat=lat, lon=lon, probability=prob))
    return predictions


def _encode_image_for_vlm_sync(image_path: str) -> tuple[str, str]:
    from PIL import Image

    with Image.open(image_path) as image:
        image = image.convert("RGB")
        image.thumbnail((1024, 1024))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii"), "image/jpeg"


async def _encode_image_for_vlm(image_path: str) -> tuple[str, str]:
    return await asyncio.to_thread(_encode_image_for_vlm_sync, image_path)


def _response_text_blocks(payload: dict[str, Any]) -> str:
    blocks = payload.get("content", [])
    texts = [block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text"]
    return "".join(texts).strip()


def _coerce_vlm_signals(payload: dict[str, Any]) -> list[VlmSignal]:
    signals: list[VlmSignal] = []
    for item in payload.get("signals", []):
        if not isinstance(item, dict):
            continue
        signal_type = str(item.get("type") or "").strip()
        description = str(item.get("description") or "").strip()
        region = str(item.get("region") or "").strip()
        confidence = str(item.get("confidence") or "").strip().lower()
        if not signal_type or not description or not region or confidence not in {"low", "medium", "high"}:
            continue
        signals.append(
            VlmSignal(
                type=signal_type,
                description=description,
                region=region,
                confidence=confidence,
            )
        )
    return signals


async def _run_haiku_vlm(
    image_path: str,
    session: SessionState,
    store: EphemeralSessionStore,
    settings: Settings,
    client: httpx.AsyncClient,
) -> list[VlmSignal]:
    if not settings.anthropic_api_key:
        raise GeolocationPipelineError("ANTHROPIC_API_KEY is not configured.")

    await _guard_budget(session, settings)
    image_data, media_type = await _encode_image_for_vlm(image_path)
    request_payload = {
        "model": settings.anthropic_haiku_model,
        "max_tokens": 300,
        "system": VISION_SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Analyze this photo.",
                    },
                ],
            }
        ],
    }

    async with ANTHROPIC_VISION_SEMAPHORE:
        await _guard_budget(session, settings)
        response = await client.post(
            str(settings.anthropic_api_url),
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": settings.anthropic_version,
                "content-type": "application/json",
            },
            json=request_payload,
            timeout=90.0,
        )
    response.raise_for_status()
    payload = response.json()
    await _record_anthropic_cost(session, store, settings.anthropic_haiku_model, payload.get("usage"), settings)

    text = _response_text_blocks(payload)
    if not text:
        return []
    parsed = json.loads(text)
    return _coerce_vlm_signals(parsed if isinstance(parsed, dict) else {})


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c


def _cluster_top_predictions(analyses: list[PostAnalysis], threshold_km: float = 50.0) -> list[GeoClipCluster]:
    clusters: list[GeoClipCluster] = []
    for analysis in analyses:
        if not analysis.geoclip_predictions:
            continue
        top_prediction = analysis.geoclip_predictions[0]
        assigned = False
        for cluster in clusters:
            distance = _haversine_km(
                top_prediction.lat,
                top_prediction.lon,
                cluster.centroid_lat,
                cluster.centroid_lon,
            )
            if distance > threshold_km:
                continue
            cluster.post_ids.append(analysis.post.post_id)
            cluster.posts.append(
                {
                    "post_id": analysis.post.post_id,
                    "media_url": analysis.post.media_url,
                    "lat": top_prediction.lat,
                    "lon": top_prediction.lon,
                    "probability": round(top_prediction.probability, 6),
                }
            )
            cluster.probabilities.append(top_prediction.probability)
            count = len(cluster.posts)
            cluster.centroid_lat = sum(post["lat"] for post in cluster.posts) / count
            cluster.centroid_lon = sum(post["lon"] for post in cluster.posts) / count
            cluster.max_radius_km = max(
                _haversine_km(cluster.centroid_lat, cluster.centroid_lon, post["lat"], post["lon"])
                for post in cluster.posts
            )
            assigned = True
            break

        if assigned:
            continue

        clusters.append(
            GeoClipCluster(
                centroid_lat=top_prediction.lat,
                centroid_lon=top_prediction.lon,
                post_ids=[analysis.post.post_id],
                posts=[
                    {
                        "post_id": analysis.post.post_id,
                        "media_url": analysis.post.media_url,
                        "lat": top_prediction.lat,
                        "lon": top_prediction.lon,
                        "probability": round(top_prediction.probability, 6),
                    }
                ],
                probabilities=[top_prediction.probability],
            )
        )
    return clusters


def _make_manual_location_finding(post: MediaPost) -> dict[str, Any]:
    location = post.location or "Tagged Instagram location"
    return {
        "pipeline": PIPELINE_NAME,
        "type": "location_inference",
        "region": location,
        "confidence_radius_km": 1,
        "confidence": "HIGH",
        "contributing_posts": [
            {
                "post_id": post.post_id,
                "media_url": post.media_url,
                "signals": [f"Instagram location tag: {location}"],
            }
        ],
        "risk_level": "HIGH",
        "remediation": (
            f"Post {post.media_url} includes an explicit Instagram location tag ({location}). "
            "Remove the location tag or delete the post if you do not want viewers to narrow your routine."
        ),
    }


def _make_exif_gps_finding(post: MediaPost, coordinates: tuple[float, float]) -> dict[str, Any]:
    lat, lon = coordinates
    region = f"GPS coordinates {lat:.6f}, {lon:.6f}"
    return {
        "pipeline": PIPELINE_NAME,
        "type": "location_inference",
        "region": region,
        "confidence_radius_km": 0,
        "confidence": "HIGH",
        "contributing_posts": [
            {
                "post_id": post.post_id,
                "media_url": post.media_url,
                "signals": [f"Embedded GPS metadata: {lat:.6f}, {lon:.6f}"],
            }
        ],
        "risk_level": "CRITICAL",
        "remediation": (
            f"Post {post.media_url} appears to retain embedded GPS coordinates. Delete it and strip metadata "
            "from the original file before re-uploading anywhere."
        ),
    }


def _make_vlm_post_finding(post: MediaPost, signals: list[VlmSignal]) -> dict[str, Any] | None:
    high_value_signals = [signal for signal in signals if signal.confidence in {"medium", "high"}]
    if not high_value_signals:
        return None
    region_counts = Counter(signal.region for signal in high_value_signals if signal.region)
    region = region_counts.most_common(1)[0][0] if region_counts else high_value_signals[0].region
    descriptions = [signal.description for signal in high_value_signals[:3]]
    return {
        "pipeline": PIPELINE_NAME,
        "type": "location_inference",
        "region": region,
        "confidence_radius_km": 50,
        "confidence": "LOW",
        "contributing_posts": [
            {
                "post_id": post.post_id,
                "media_url": post.media_url,
                "signals": descriptions,
            }
        ],
        "risk_level": "MEDIUM",
        "remediation": (
            f"Post {post.media_url} contains visible location cues ({'; '.join(descriptions)}). "
            "Crop or blur identifying background details before re-uploading."
        ),
    }


def _risk_level_for_confidence(confidence: str) -> str:
    normalized = confidence.upper()
    if normalized == "HIGH":
        return "HIGH"
    if normalized == "MEDIUM":
        return "HIGH"
    return "MEDIUM"


def _build_aggregation_remediation(
    region: str,
    contributing_posts: list[dict[str, Any]],
) -> str:
    urls = [post.get("media_url", "") for post in contributing_posts if post.get("media_url")]
    quoted_urls = " and ".join(urls[:2]) if len(urls) > 1 else (urls[0] if urls else "these posts")
    signals: list[str] = []
    for post in contributing_posts:
        for signal in post.get("signals", []):
            if isinstance(signal, str) and signal not in signals:
                signals.append(signal)
    signal_text = ", ".join(signals[:3]) or "corroborating location cues"
    return (
        f"Posts {quoted_urls} contain {signal_text} narrowing your location to the {region} area. "
        "Delete them or crop the identifying background before re-uploading."
    )


async def _run_sonnet_aggregation(
    analyses: list[PostAnalysis],
    session: SessionState,
    store: EphemeralSessionStore,
    settings: Settings,
    client: httpx.AsyncClient,
) -> list[dict[str, Any]]:
    if not settings.anthropic_api_key:
        raise GeolocationPipelineError("ANTHROPIC_API_KEY is not configured.")

    await _guard_budget(session, settings)

    location_tags = [
        {
            "post_id": analysis.post.post_id,
            "media_url": analysis.post.media_url,
            "location": analysis.post.location,
        }
        for analysis in analyses
        if analysis.post.location
    ]
    geoclip_clusters = [
        {
            "centroid_lat": round(cluster.centroid_lat, 6),
            "centroid_lon": round(cluster.centroid_lon, 6),
            "post_count": len(cluster.posts),
            "max_radius_km": round(cluster.max_radius_km, 2),
            "average_probability": round(sum(cluster.probabilities) / len(cluster.probabilities), 6),
            "posts": cluster.posts,
        }
        for cluster in _cluster_top_predictions(analyses, threshold_km=50.0)
    ]
    vlm_signals = [
        {
            "post_id": analysis.post.post_id,
            "media_url": analysis.post.media_url,
            "signals": [
                {
                    "type": signal.type,
                    "description": signal.description,
                    "region": signal.region,
                    "confidence": signal.confidence,
                }
                for signal in analysis.vlm_signals
            ],
        }
        for analysis in analyses
        if analysis.vlm_signals
    ]

    request_payload = {
        "model": settings.anthropic_sonnet_model,
        "max_tokens": 800,
        "system": (
            "You aggregate geolocation evidence from Instagram posts. Return JSON only with schema "
            '{"probable_regions":[{"region":string,"confidence_radius_km":number,"confidence_tier":"LOW"|"MEDIUM"|"HIGH","contributing_posts":[{"post_id":string,"media_url":string,"signals":[string]}]}]}. '
            "Use only the provided evidence. Do not invent places or evidence. Do not promote a finding without support."
        ),
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "confidence_rules": {
                                    "manual_location_tag": "HIGH",
                                    "multiple_corroborating_vlm_across_posts": "HIGH",
                                    "geoclip_cluster_5_posts_within_100km": "MEDIUM",
                                    "single_strong_vlm_signal": "LOW",
                                    "single_geoclip_prediction": "LOW",
                                    "exif_gps": "already handled separately; do not duplicate",
                                },
                                "merge_threshold_km": 50,
                                "location_tags": location_tags,
                                "geoclip_clusters": geoclip_clusters,
                                "vlm_signals_by_post": vlm_signals,
                            }
                        ),
                    }
                ],
            }
        ],
    }

    response = await client.post(
        str(settings.anthropic_api_url),
        headers={
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": settings.anthropic_version,
            "content-type": "application/json",
        },
        json=request_payload,
        timeout=90.0,
    )
    response.raise_for_status()
    payload = response.json()
    await _record_anthropic_cost(session, store, settings.anthropic_sonnet_model, payload.get("usage"), settings)

    text = _response_text_blocks(payload)
    if not text:
        return []
    parsed = json.loads(text)
    probable_regions = parsed.get("probable_regions", []) if isinstance(parsed, dict) else []
    findings: list[dict[str, Any]] = []
    for item in probable_regions:
        if not isinstance(item, dict):
            continue
        region = str(item.get("region") or "").strip()
        confidence = str(item.get("confidence_tier") or "").strip().upper()
        radius = item.get("confidence_radius_km")
        contributing_posts = item.get("contributing_posts", [])
        if not region or confidence not in {"LOW", "MEDIUM", "HIGH"} or not isinstance(contributing_posts, list):
            continue
        cleaned_posts: list[dict[str, Any]] = []
        for post in contributing_posts:
            if not isinstance(post, dict):
                continue
            cleaned_posts.append(
                {
                    "post_id": str(post.get("post_id") or ""),
                    "media_url": str(post.get("media_url") or ""),
                    "signals": [str(signal) for signal in post.get("signals", []) if isinstance(signal, str)],
                }
            )
        findings.append(
            {
                "pipeline": PIPELINE_NAME,
                "type": "location_inference",
                "region": region,
                "confidence_radius_km": int(radius) if isinstance(radius, (int, float)) else 100,
                "confidence": confidence,
                "contributing_posts": cleaned_posts,
                "risk_level": _risk_level_for_confidence(confidence),
                "remediation": _build_aggregation_remediation(region, cleaned_posts),
            }
        )
    return findings


async def _download_post_image(
    client: httpx.AsyncClient,
    post: MediaPost,
) -> str:
    response = await client.get(post.media_url, timeout=60.0, follow_redirects=True)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    suffix = mimetypes.guess_extension(content_type.split(";")[0].strip()) or Path(post.media_url).suffix or ".jpg"
    fd, path = tempfile.mkstemp(prefix=f"shieldclaw-{post.post_id}-", suffix=suffix)
    os.close(fd)
    await asyncio.to_thread(Path(path).write_bytes, response.content)
    return path


async def _process_post(
    post: MediaPost,
    session: SessionState,
    store: EphemeralSessionStore,
    settings: Settings,
    client: httpx.AsyncClient,
) -> PostAnalysis:
    analysis = PostAnalysis(post=post)

    if post.location:
        await _publish_finding(session, store, _make_manual_location_finding(post))

    async with POST_ANALYSIS_SEMAPHORE:
        image_path: str | None = None
        try:
            image_path = await _download_post_image(client, post)
            analysis.exif_coordinates = await _extract_exif_gps(image_path)
            if analysis.exif_coordinates:
                await _publish_finding(session, store, _make_exif_gps_finding(post, analysis.exif_coordinates))

            analysis.geoclip_predictions = await _run_geoclip(image_path)

            try:
                analysis.vlm_signals = await _run_haiku_vlm(image_path, session, store, settings, client)
            except BudgetExceededError:
                logger.info("Skipping VLM analysis for %s because the audit budget is exhausted", post.post_id)
            except Exception:
                logger.exception("VLM analysis failed for post %s", post.post_id)

            vlm_finding = _make_vlm_post_finding(post, analysis.vlm_signals)
            if vlm_finding:
                await _publish_finding(session, store, vlm_finding)
        finally:
            if image_path:
                try:
                    os.unlink(image_path)
                except FileNotFoundError:
                    pass

    return analysis


async def _fetch_recent_media(
    session: SessionState,
    settings: Settings,
    client: httpx.AsyncClient,
) -> list[MediaPost]:
    token = _extract_access_token(session)
    cached_media = _extract_cached_media(session)
    if not token and cached_media:
        return _normalize_media_items(cached_media)
    if not token:
        raise GeolocationPipelineError("Session does not contain a Meta Graph access token.")

    requested_fields = "id,media_url,thumbnail_url,caption,timestamp,location"
    fallback_fields = "id,media_url,thumbnail_url,caption,timestamp"
    results: list[dict[str, Any]] = []
    next_url = f"{str(settings.instagram_graph_api_base).rstrip('/')}/me/media"
    params: dict[str, Any] | None = {
        "fields": requested_fields,
        "limit": MAX_MEDIA_POSTS,
        "access_token": token,
    }
    used_fallback = False

    while next_url and len(results) < MAX_MEDIA_POSTS:
        response = await client.get(next_url, params=params, timeout=60.0)
        if response.status_code == 400 and not used_fallback and "location" in requested_fields:
            payload = response.json()
            error_message = json.dumps(payload)
            if "location" in error_message:
                used_fallback = True
                params = {
                    "fields": fallback_fields,
                    "limit": MAX_MEDIA_POSTS,
                    "access_token": token,
                }
                next_url = f"{str(settings.instagram_graph_api_base).rstrip('/')}/me/media"
                results.clear()
                continue
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("data", [])
        if isinstance(batch, list):
            results.extend(item for item in batch if isinstance(item, dict))
        paging = payload.get("paging", {})
        next_url = paging.get("next") if isinstance(paging, dict) else None
        params = None

    return _normalize_media_items(results[:MAX_MEDIA_POSTS])


async def run_geolocation_audit(
    session: SessionState,
    store: EphemeralSessionStore,
    settings: Settings,
) -> None:
    async with httpx.AsyncClient() as client:
        try:
            await _publish_pipeline_status(session, store, "running", cost_usd_total=round(_session_total_cost(session), 6))
            posts = await _fetch_recent_media(session, settings, client)
            await _publish_pipeline_status(session, store, "media_loaded", total_posts=len(posts))

            if not posts:
                await _publish_pipeline_status(session, store, "completed", total_posts=0, findings_count=0)
                return

            analyses = await asyncio.gather(
                *[
                    _process_post(post, session, store, settings, client)
                    for post in posts
                ],
                return_exceptions=True,
            )

            successful_analyses: list[PostAnalysis] = []
            for result in analyses:
                if isinstance(result, Exception):
                    logger.exception("Post analysis failed", exc_info=result)
                    continue
                successful_analyses.append(result)

            if _session_total_cost(session) >= settings.audit_cost_ceiling_usd:
                await _publish_pipeline_status(
                    session,
                    store,
                    "budget_exceeded",
                    total_posts=len(posts),
                    processed_posts=len(successful_analyses),
                    cost_usd_total=round(_session_total_cost(session), 6),
                    budget_usd=settings.audit_cost_ceiling_usd,
                )
                return

            try:
                aggregated_findings = await _run_sonnet_aggregation(
                    successful_analyses,
                    session,
                    store,
                    settings,
                    client,
                )
            except BudgetExceededError:
                await _publish_pipeline_status(
                    session,
                    store,
                    "budget_exceeded",
                    total_posts=len(posts),
                    processed_posts=len(successful_analyses),
                    cost_usd_total=round(_session_total_cost(session), 6),
                    budget_usd=settings.audit_cost_ceiling_usd,
                )
                return

            for finding in aggregated_findings:
                await _publish_finding(session, store, finding)

            findings_count = len(session.data.get("findings", []))
            await _publish_pipeline_status(
                session,
                store,
                "completed",
                total_posts=len(posts),
                processed_posts=len(successful_analyses),
                findings_count=findings_count,
                cost_usd_total=round(_session_total_cost(session), 6),
            )
        except BudgetExceededError:
            await _publish_pipeline_status(
                session,
                store,
                "budget_exceeded",
                cost_usd_total=round(_session_total_cost(session), 6),
                budget_usd=settings.audit_cost_ceiling_usd,
            )
        except Exception as exc:
            logger.exception("Geolocation audit failed for session %s", session.session_id)
            await _publish_pipeline_status(session, store, "error", error=str(exc))
        finally:
            async with session.state_lock:
                session.audit_tasks.pop(PIPELINE_NAME, None)


async def ensure_geolocation_audit_task(
    session: SessionState,
    store: EphemeralSessionStore,
    settings: Settings,
) -> asyncio.Task[None]:
    async with session.state_lock:
        existing_task = session.audit_tasks.get(PIPELINE_NAME)
        if existing_task and not existing_task.done():
            return existing_task
        task = asyncio.create_task(run_geolocation_audit(session, store, settings))
        session.audit_tasks[PIPELINE_NAME] = task
        return task
