"""SSE event builders — locked JSON shapes shared across instances."""

from __future__ import annotations

import re
from typing import Any, Literal

PipelineName = Literal["identity", "geolocation", "web_footprint"]
PipelineStatus = Literal[
    "idle", "running", "complete", "error", "budget_exceeded"
]


# Keep AI-provider names out of user-visible strings so the dashboard
# doesn't expose which upstream we're calling. Applied to any free-form
# `detail` / `summary` string before it leaves the backend.
_PROVIDER_SCRUB = (
    (re.compile(r"GEMINI_API_KEY", re.IGNORECASE), "AI_API_KEY"),
    (re.compile(r"\bGeminiError\b"), "ServiceError"),
    (re.compile(r"\bGemini\b", re.IGNORECASE), "AI"),
)


def _scrub_provider_names(text: str | None) -> str | None:
    if not text:
        return text
    out = text
    for pattern, replacement in _PROVIDER_SCRUB:
        out = pattern.sub(replacement, out)
    return out


def make_event(event_type: str, payload: dict) -> dict:
    """Wrap an event payload with its discriminator."""
    return {"type": event_type, "data": payload}


def profile_loaded(profile: dict, post_count: int) -> dict:
    return make_event(
        "profile_loaded", {"profile": profile, "post_count": post_count}
    )


def pipeline_status(
    pipeline: PipelineName, status: PipelineStatus, detail: str | None = None
) -> dict:
    return make_event(
        "pipeline_status",
        {
            "pipeline": pipeline,
            "status": status,
            "detail": _scrub_provider_names(detail),
        },
    )


def finding(pipeline: str, finding_dict: dict) -> dict:
    return make_event("finding", {"pipeline": pipeline, "finding": finding_dict})


def cost_update(cost: float) -> dict:
    return make_event("cost_update", {"cost": round(float(cost), 6)})


def aggregator_done(
    exposure_score: int,
    summary: str,
    remediation_list: list[dict[str, Any]],
) -> dict:
    return make_event(
        "aggregator_done",
        {
            "exposure_score": int(exposure_score),
            "summary": _scrub_provider_names(summary) or "",
            "remediation_list": remediation_list,
        },
    )


def stream_done() -> dict:
    return make_event("stream_done", {})
