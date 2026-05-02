"""In-memory session store backing the audit pipeline.

Sessions are short-lived; nothing is persisted to disk or DB. Each
``SessionState`` carries the running httpx client, the in-flight pipeline
tasks, the event log used by the SSE stream, and an asyncio Condition that
lets the stream block efficiently until new events arrive.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class SessionState:
    session_id: str
    created_at: datetime
    updated_at: datetime
    data: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    event_condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    audit_tasks: dict[str, asyncio.Task] = field(default_factory=dict)

    async def publish_event(self, event: dict) -> None:
        async with self.event_condition:
            self.events.append(event)
            self.updated_at = datetime.now(timezone.utc)
            self.event_condition.notify_all()

    async def wait_for_events(
        self, last_index: int, timeout_seconds: float = 15.0
    ) -> None:
        async with self.event_condition:
            if len(self.events) > last_index:
                return
            await asyncio.wait_for(
                self.event_condition.wait(), timeout=timeout_seconds
            )


SESSIONS: dict[str, SessionState] = {}


def create_session() -> SessionState:
    session_id = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    state = SessionState(session_id=session_id, created_at=now, updated_at=now)
    SESSIONS[session_id] = state
    return state


def get_session(session_id: str) -> SessionState | None:
    return SESSIONS.get(session_id)


def delete_session(session_id: str) -> None:
    state = SESSIONS.pop(session_id, None)
    if state is None:
        return
    for task in state.audit_tasks.values():
        if not task.done():
            task.cancel()
    http = state.data.get("http")
    if http is not None:
        try:
            asyncio.create_task(http.aclose())
        except RuntimeError:
            # No running loop at shutdown — best effort.
            pass


async def gc_loop(ttl_minutes: int, interval_seconds: float = 60.0) -> None:
    """Background coroutine that evicts sessions older than ``ttl_minutes``."""
    ttl = timedelta(minutes=ttl_minutes)
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            cutoff = datetime.now(timezone.utc) - ttl
            stale = [
                sid
                for sid, state in SESSIONS.items()
                if state.updated_at < cutoff
            ]
            for sid in stale:
                delete_session(sid)
    except asyncio.CancelledError:
        # Clean shutdown via lifespan.
        for sid in list(SESSIONS.keys()):
            delete_session(sid)
        raise
