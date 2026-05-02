from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.core.dependencies import get_session_store
from app.core.session_store import EphemeralSessionStore, SessionState
from app.pipelines.web_footprint import PIPELINE_NAME, ensure_web_footprint_audit_task

router = APIRouter(tags=["audit"])


def _sse_encode(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _stream_session_events(
    session: SessionState,
    audit_task: asyncio.Task[None],
    start_index: int,
) -> AsyncIterator[str]:
    last_index = start_index
    yield _sse_encode({"pipeline": PIPELINE_NAME, "pipeline_status": "subscribed"})

    while True:
        while last_index < len(session.events):
            event = session.events[last_index]
            last_index += 1
            if event.get("pipeline") == PIPELINE_NAME:
                yield _sse_encode(event)

        if audit_task.done():
            break

        try:
            await session.wait_for_events(last_index)
        except TimeoutError:
            yield ": keep-alive\n\n"

    while last_index < len(session.events):
        event = session.events[last_index]
        last_index += 1
        if event.get("pipeline") == PIPELINE_NAME:
            yield _sse_encode(event)


@router.get("/audit/{session_id}/stream")
async def stream_audit(
    session_id: str,
    settings: Settings = Depends(get_settings),
    store: EphemeralSessionStore = Depends(get_session_store),
) -> StreamingResponse:
    session = await store.get(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit session not found.",
        )

    start_index = 0
    audit_task = await ensure_web_footprint_audit_task(session, settings)
    return StreamingResponse(
        _stream_session_events(session, audit_task, start_index),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
