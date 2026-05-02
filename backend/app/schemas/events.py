"""SSE event builders — locked JSON shapes shared across instances."""

from __future__ import annotations

from typing import Any, Literal

PipelineName = Literal["identity", "geolocation", "web_footprint"]
PipelineStatus = Literal[
    "idle", "running", "complete", "error", "budget_exceeded"
]


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
        {"pipeline": pipeline, "status": status, "detail": detail},
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
            "summary": summary,
            "remediation_list": remediation_list,
        },
    )


def stream_done() -> dict:
    return make_event("stream_done", {})
