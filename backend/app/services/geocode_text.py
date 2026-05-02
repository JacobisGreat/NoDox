"""Embedding-based text geocoder.

Turns a free-text location hint ("Eiffel Tower", "Brooklyn", "Bali") into
``(lat, lon)`` so geolocation findings can plot on the map regardless of
which channel produced them. Two-stage matcher:

1. **Fast path** — exact / substring match against corpus names + aliases.
   Most Instagram location-tag values resolve here without any model.
2. **Embedding path** — falls back to sentence-transformers cosine
   similarity against a precomputed corpus matrix. We reuse the same
   ``all-MiniLM-L6-v2`` model the identity pipeline already loads.

The corpus lives at ``app/data/geocode_corpus.json``: ~300 world cities
plus ~50 globally recognizable landmarks. It's enough to cover the
common Instagram audit case offline; misses just return ``None`` and the
caller skips coordinate enrichment for that finding.

The cache is module-global and async-safe — first call initializes,
later calls reuse. Embeddings are computed lazily on first use, so
audits that produce no geocodable text never pay the cost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_CORPUS_PATH = Path(__file__).resolve().parents[1] / "data" / "geocode_corpus.json"
_DEFAULT_THRESHOLD = 0.55
_LRU_LIMIT = 256


@dataclass(frozen=True)
class GeocodeHit:
    name: str
    country: str
    lat: float
    lon: float
    kind: str
    confidence: float
    method: str  # "exact", "substring", or "embedding"


# ---------------------------------------------------------------------------
# Corpus + lazy embedding cache.
# ---------------------------------------------------------------------------


_corpus_entries: list[dict[str, Any]] | None = None
_corpus_lookup: dict[str, dict[str, Any]] = {}
_corpus_substr_index: list[tuple[str, dict[str, Any]]] = []
_corpus_load_lock = asyncio.Lock()

_embeddings_matrix: Any = None  # numpy.ndarray once computed
_embeddings_load_attempted = False
_embeddings_lock = asyncio.Lock()

_lookup_cache: dict[str, GeocodeHit | None] = {}


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[\s,/_\-]+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text.strip()


def _entry_keys(entry: dict[str, Any]) -> list[str]:
    keys = [entry["name"]]
    aliases = entry.get("aliases") or []
    if isinstance(aliases, list):
        keys.extend(str(a) for a in aliases if a)
    return keys


def _entry_embedding_text(entry: dict[str, Any]) -> str:
    country = entry.get("country") or ""
    return f"{entry['name']}, {country}".strip(", ")


def _load_corpus_sync() -> list[dict[str, Any]]:
    try:
        with open(_CORPUS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning("Failed to load geocode corpus: %s", exc)
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    out: list[dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        try:
            lat = float(e["lat"])
            lon = float(e["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(
            {
                "name": str(e.get("name", "")),
                "country": str(e.get("country", "") or ""),
                "lat": lat,
                "lon": lon,
                "kind": str(e.get("kind", "city") or "city"),
                "aliases": e.get("aliases") or [],
            }
        )
    return out


async def _ensure_corpus() -> list[dict[str, Any]]:
    global _corpus_entries
    if _corpus_entries is not None:
        return _corpus_entries
    async with _corpus_load_lock:
        if _corpus_entries is not None:
            return _corpus_entries
        entries = await asyncio.to_thread(_load_corpus_sync)
        # Build lookup indices.
        lookup: dict[str, dict[str, Any]] = {}
        substr: list[tuple[str, dict[str, Any]]] = []
        for entry in entries:
            for key in _entry_keys(entry):
                norm = _normalize(key)
                if not norm:
                    continue
                lookup.setdefault(norm, entry)
                substr.append((norm, entry))
        # Longest keys first — substring matcher prefers more specific hits.
        substr.sort(key=lambda kv: len(kv[0]), reverse=True)
        _corpus_lookup.update(lookup)
        _corpus_substr_index.extend(substr)
        _corpus_entries = entries
    return _corpus_entries


# ---------------------------------------------------------------------------
# Embedding init (lazy, shares the identity pipeline's MiniLM model).
# ---------------------------------------------------------------------------


def _compute_embeddings_sync(model: Any, texts: list[str]) -> Any:
    return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)


async def _ensure_embeddings(entries: list[dict[str, Any]]) -> Any:
    global _embeddings_matrix, _embeddings_load_attempted
    if _embeddings_matrix is not None or _embeddings_load_attempted:
        return _embeddings_matrix
    async with _embeddings_lock:
        if _embeddings_matrix is not None or _embeddings_load_attempted:
            return _embeddings_matrix
        _embeddings_load_attempted = True
        try:
            from app.pipelines.identity import _get_sentence_model

            model = await _get_sentence_model()
            if model is None:
                logger.info("geocode_text: sentence model unavailable — embedding pass disabled")
                return None
            texts = [_entry_embedding_text(e) for e in entries]
            matrix = await asyncio.to_thread(_compute_embeddings_sync, model, texts)
            _embeddings_matrix = matrix
        except Exception as exc:
            logger.warning("geocode_text: embedding init failed: %s", exc)
            _embeddings_matrix = None
    return _embeddings_matrix


async def _embed_query(text: str) -> Any | None:
    try:
        from app.pipelines.identity import _get_sentence_model

        model = await _get_sentence_model()
        if model is None:
            return None
        return await asyncio.to_thread(_compute_embeddings_sync, model, [text])
    except Exception as exc:
        logger.debug("geocode_text: query embed failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def _hit_from_entry(
    entry: dict[str, Any], confidence: float, method: str
) -> GeocodeHit:
    return GeocodeHit(
        name=entry["name"],
        country=entry["country"],
        lat=entry["lat"],
        lon=entry["lon"],
        kind=entry.get("kind", "city"),
        confidence=max(0.0, min(1.0, confidence)),
        method=method,
    )


def _exact_or_substring(norm: str) -> tuple[dict[str, Any], str, float] | None:
    """Return (entry, method, confidence) or None."""
    if not norm:
        return None
    direct = _corpus_lookup.get(norm)
    if direct is not None:
        return direct, "exact", 0.95
    # Substring: prefer longer keys (already sorted desc).
    tokens = norm.split()
    for key, entry in _corpus_substr_index:
        if not key:
            continue
        if key in norm or norm in key:
            # Confidence reflects fractional overlap.
            longer = max(len(key), len(norm))
            shorter = min(len(key), len(norm))
            overlap = shorter / longer if longer else 0.0
            if len(key.split()) >= 2 or any(t == key for t in tokens):
                return entry, "substring", 0.6 + 0.3 * overlap
    return None


async def _embedding_match(
    text: str, entries: list[dict[str, Any]], threshold: float
) -> tuple[dict[str, Any], float] | None:
    matrix = await _ensure_embeddings(entries)
    if matrix is None:
        return None
    query = await _embed_query(text)
    if query is None:
        return None
    try:
        # Cosine similarity since both are L2-normalized.
        scores = matrix @ query[0]
        best_idx = int(scores.argmax())
        best_score = float(scores[best_idx])
    except Exception as exc:
        logger.debug("geocode_text: similarity compute failed: %s", exc)
        return None
    if best_score < threshold:
        return None
    return entries[best_idx], best_score


async def geocode_text(
    text: str,
    *,
    threshold: float = _DEFAULT_THRESHOLD,
) -> GeocodeHit | None:
    """Return the best ``GeocodeHit`` for ``text`` or ``None`` on a miss.

    Tries exact/substring against the corpus first, falls back to a
    sentence-embedding similarity match if that doesn't resolve. ``None``
    is returned for empty input, an empty corpus, or no match above the
    embedding similarity threshold.
    """
    if not text or not text.strip():
        return None
    cache_key = text.strip().lower()
    cached = _lookup_cache.get(cache_key)
    if cached is not None or cache_key in _lookup_cache:
        return cached

    entries = await _ensure_corpus()
    if not entries:
        _lookup_cache[cache_key] = None
        return None

    hit: GeocodeHit | None = None
    norm = _normalize(text)
    fast = _exact_or_substring(norm)
    if fast is not None:
        entry, method, confidence = fast
        hit = _hit_from_entry(entry, confidence, method)
    else:
        match = await _embedding_match(text, entries, threshold)
        if match is not None:
            entry, score = match
            hit = _hit_from_entry(entry, score, "embedding")

    # LRU-ish cap: drop the oldest item once full.
    if len(_lookup_cache) >= _LRU_LIMIT:
        try:
            first_key = next(iter(_lookup_cache))
            _lookup_cache.pop(first_key, None)
        except StopIteration:
            pass
    _lookup_cache[cache_key] = hit
    return hit


async def warmup() -> None:
    """Pre-load the corpus + embedding matrix during FastAPI startup so the
    first audit doesn't pay the cold-start cost. Safe to call repeatedly."""
    entries = await _ensure_corpus()
    if entries:
        await _ensure_embeddings(entries)
