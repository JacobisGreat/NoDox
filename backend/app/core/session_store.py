import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SessionState:
    session_id: str
    created_at: datetime
    updated_at: datetime
    auth_status: str = "unauthenticated"
    gate_passed: bool = False
    oauth_state: str | None = None
    access_token: str | None = None
    token_expires_at: datetime | None = None
    ig_user: dict[str, Any] | None = None
    media: list[dict[str, Any]] = field(default_factory=list)
    last_error: str | None = None

    def touch(self) -> None:
        self.updated_at = utcnow()


class EphemeralSessionStore:
    def __init__(self, ttl_minutes: int = 120) -> None:
        self._ttl = timedelta(minutes=ttl_minutes)
        self._sessions: dict[str, SessionState] = {}
        self._lock = asyncio.Lock()

    async def _cleanup_locked(self) -> None:
        now = utcnow()
        expired_keys = [
            key
            for key, session in self._sessions.items()
            if now - session.updated_at > self._ttl
        ]
        for key in expired_keys:
            del self._sessions[key]

    async def get_or_create(self, session_id: str | None = None) -> SessionState:
        async with self._lock:
            await self._cleanup_locked()
            if session_id and session_id in self._sessions:
                session = self._sessions[session_id]
                session.touch()
                return session

            new_session_id = secrets.token_urlsafe(32)
            now = utcnow()
            session = SessionState(
                session_id=new_session_id,
                created_at=now,
                updated_at=now,
            )
            self._sessions[new_session_id] = session
            return session

    async def get(self, session_id: str | None) -> SessionState | None:
        if not session_id:
            return None
        async with self._lock:
            await self._cleanup_locked()
            session = self._sessions.get(session_id)
            if session:
                session.touch()
            return session

    async def save(self, session: SessionState) -> SessionState:
        async with self._lock:
            await self._cleanup_locked()
            session.touch()
            self._sessions[session.session_id] = session
            return session

    async def clear(self, session_id: str | None) -> None:
        if not session_id:
            return
        async with self._lock:
            self._sessions.pop(session_id, None)

