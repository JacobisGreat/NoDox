from typing import Any

from pydantic import BaseModel


class OAuthStartResponse(BaseModel):
    auth_url: str
    session_id: str
    gate_required: str = "OAuth complete + GET /me/media success"


class SessionView(BaseModel):
    session_id: str
    auth_status: str
    gate_passed: bool
    ig_user: dict[str, Any] | None
    media_count: int
    last_error: str | None


class MediaGateResponse(BaseModel):
    gate_passed: bool
    media: list[dict[str, Any]]

