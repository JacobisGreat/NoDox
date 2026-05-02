"""POST /api/audit/{session_id}/start — kick off the three pipelines."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.schemas.events import (
    pipeline_status as _pipeline_status_event,
)
from app.schemas.events import (
    profile_loaded as _profile_loaded_event,
)
from app.schemas.events import (
    stream_done as _stream_done_event,
)
from app.services.aggregator import run as run_aggregator
from app.services.anthropic_client import AnthropicClient
from app.services.cost_tracker import CostTracker
from app.services.image_downloader import ImageDownloader
from app.session_store import get_session

# Pipeline modules — geolocation/web_footprint are written by instance 2,
# but we always import them so the orchestrator stays uniform.
from app.pipelines import identity as identity_pipeline
from app.pipelines import geolocation as geolocation_pipeline
from app.pipelines import media as media_pipeline
from app.pipelines import web_footprint as web_footprint_pipeline

router = APIRouter(prefix="/audit", tags=["audit"])


PIPELINES: dict[str, Any] = {
    "identity": identity_pipeline.run,
    "geolocation": geolocation_pipeline.run,
    "media": media_pipeline.run,
    "web_footprint": web_footprint_pipeline.run,
}


def _build_session_services(session) -> None:
    """Lazy-create the per-session http client / Anthropic / cost tracker."""
    settings = get_settings()
    if "http" not in session.data:
        session.data["http"] = httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; NODOXX-self-audit/0.1; "
                    "+https://github.com/anthropics/claude-code)"
                )
            },
            follow_redirects=True,
            timeout=httpx.Timeout(15.0, connect=10.0),
        )
    http: httpx.AsyncClient = session.data["http"]

    if "anthropic" not in session.data:
        session.data["anthropic"] = AnthropicClient(
            api_key=settings.anthropic_api_key,
            api_url=settings.anthropic_api_url,
            version=settings.anthropic_version,
            http=http,
        )

    if "image_downloader" not in session.data:
        session.data["image_downloader"] = ImageDownloader(http=http)

    if "cost_tracker" not in session.data:
        session.data["cost_tracker"] = CostTracker(
            global_ceiling_usd=settings.audit_cost_ceiling_usd,
            scope_budgets={
                "web_footprint": settings.web_footprint_budget_share_usd,
            },
            sonnet_in=settings.claude_sonnet_input_cost_per_million,
            sonnet_out=settings.claude_sonnet_output_cost_per_million,
            haiku_in=settings.claude_haiku_input_cost_per_million,
            haiku_out=settings.claude_haiku_output_cost_per_million,
            sonnet_model_id=settings.anthropic_sonnet_model,
            haiku_model_id=settings.anthropic_haiku_model,
        )

    if "anthropic_semaphore" not in session.data:
        # Shared across all pipelines, capped at 5 concurrent vision/text
        # calls so we don't hammer the Anthropic API in any one audit.
        session.data["anthropic_semaphore"] = asyncio.Semaphore(5)
        session.data["vision_semaphore"] = session.data["anthropic_semaphore"]

    session.data.setdefault("findings", [])
    session.data.setdefault("pipeline_outcomes", {})


async def _coordinator(session) -> None:
    """Wait for all pipeline tasks, run the aggregator, emit stream_done."""
    pipeline_tasks: dict[str, asyncio.Task] = {
        name: session.audit_tasks[name]
        for name in PIPELINES
        if name in session.audit_tasks
    }
    try:
        await asyncio.gather(
            *pipeline_tasks.values(), return_exceptions=True
        )
    except asyncio.CancelledError:
        for t in pipeline_tasks.values():
            if not t.done():
                t.cancel()
        raise

    outcomes: dict[str, str] = session.data.setdefault("pipeline_outcomes", {})
    for name, task in pipeline_tasks.items():
        if task.cancelled():
            outcomes.setdefault(name, "error")
        elif task.exception() is not None:
            outcomes.setdefault(name, "error")
            await session.publish_event(
                _pipeline_status_event(
                    name, "error", str(task.exception())[:300]
                )
            )
        else:
            outcomes.setdefault(name, "complete")

    findings = session.data.get("findings") or []
    if findings:
        try:
            await run_aggregator(session)
        except Exception as exc:
            # Aggregator errors must not block stream_done.
            await session.publish_event(
                _pipeline_status_event(
                    "identity", "error", f"aggregator failure: {exc}"
                )
            )

    await session.publish_event(_stream_done_event())


@router.post("/{session_id}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_audit(session_id: str, request: Request) -> JSONResponse:
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session")

    async with session.state_lock:
        if session.data.get("audit_started"):
            raise HTTPException(status_code=409, detail="Audit already started")
        session.data["audit_started"] = True
        _build_session_services(session)

    profile = session.data.get("profile") or {}
    posts = session.data.get("posts") or []
    await session.publish_event(_profile_loaded_event(profile, len(posts)))

    # Mark all three pipelines as idle up front so the dashboard renders
    # placeholders instantly.
    for name in PIPELINES:
        await session.publish_event(_pipeline_status_event(name, "idle"))

    for name, runner in PIPELINES.items():
        task = asyncio.create_task(runner(session), name=f"pipeline:{name}")
        session.audit_tasks[name] = task

    coordinator_task = asyncio.create_task(
        _coordinator(session), name="coordinator"
    )
    session.audit_tasks["__coordinator__"] = coordinator_task

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"session_id": session_id, "started": True},
    )
