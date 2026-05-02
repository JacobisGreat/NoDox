"""Loader for static JSON assets (platforms list, etc.)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def load_platforms() -> list[dict[str, Any]]:
    """Return the full platform descriptor list from ``platforms.json``.

    Each entry has the shape::

        {
            "name": str,
            "url_template": str,        # contains "{username}"
            "method": "GET" | "HEAD",
            "valid_status": [int, ...],
            "error_indicators": [str, ...],
            "category": str,
        }
    """
    path = _DATA_DIR / "platforms.json"
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError("platforms.json must contain a JSON array")
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if "url_template" not in entry or "name" not in entry:
            continue
        cleaned.append(
            {
                "name": str(entry.get("name", "")),
                "url_template": str(entry.get("url_template", "")),
                "method": str(entry.get("method", "GET")).upper() or "GET",
                "valid_status": list(entry.get("valid_status", [200])),
                "error_indicators": list(entry.get("error_indicators", [])),
                "category": str(entry.get("category", "other")),
            }
        )
    return cleaned


def render_url(template: str, username: str) -> str:
    return template.replace("{username}", username)
