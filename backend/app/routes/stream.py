"""GET /api/audit/{session_id}/stream — Server-Sent Events.

Streams every event in ``session.events`` to the browser, blocking on a
condition variable in between batches and emitting comment-only keepalive
frames every ~15s so intermediate proxies don't drop the connection.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.session_store import get_session

router = APIRouter(prefix="/audit", tags=["audit"])

_KEEPALIVE_TIMEOUT = 15.0


def _format_sse(event: dict) -> str:
    event_type = event.get("type") or "message"
    data = event.get("data", {})
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/{session_id}/stream")
async def stream_audit(session_id: str, request: Request) -> StreamingResponse:
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session")

    async def event_iterator() -> AsyncIterator[bytes]:
        cursor = 0
        try:
            while True:
                if await request.is_disconnected():
                    break

                # Drain whatever is in the buffer.
                while cursor < len(session.events):
                    event = session.events[cursor]
                    cursor += 1
                    yield _format_sse(event).encode("utf-8")
                    if event.get("type") == "stream_done":
                        return

                # Block until something new shows up, or send a keepalive.
                try:
                    await session.wait_for_events(
                        cursor, timeout_seconds=_KEEPALIVE_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
        except asyncio.CancelledError:
            return

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        event_iterator(), media_type="text/event-stream", headers=headers
    )
