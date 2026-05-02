"""POST /api/profile/fetch — anonymous Instagram lookup."""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.instaloader_fetch import (
    InstaFetchError,
    InstaProfileNotFound,
    InstaProfilePrivate,
    InstaRateLimited,
    fetch_profile_and_posts,
)
from app.session_store import create_session

router = APIRouter(prefix="/profile", tags=["profile"])

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._]{1,30}$")


class FetchRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=30)


class FetchResponse(BaseModel):
    session_id: str
    profile: dict
    post_count: int


@router.post("/fetch", response_model=FetchResponse)
async def fetch_profile(payload: FetchRequest) -> FetchResponse:
    username = payload.username.strip().lstrip("@").lower()
    if not _USERNAME_RE.match(username):
        raise HTTPException(
            status_code=422,
            detail="Username must match ^[a-zA-Z0-9._]{1,30}$",
        )

    try:
        result = await fetch_profile_and_posts(username, max_posts=50)
    except InstaProfileNotFound:
        raise HTTPException(
            status_code=404, detail=f"Instagram profile '{username}' not found"
        )
    except InstaProfilePrivate:
        raise HTTPException(
            status_code=422, detail="Cannot audit private profiles"
        )
    except InstaRateLimited:
        raise HTTPException(
            status_code=429,
            detail="Instagram rate limit hit — try again shortly",
            headers={"Retry-After": "60"},
        )
    except InstaFetchError as exc:
        raise HTTPException(status_code=500, detail=f"Fetch failed: {exc}")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")

    if result.profile.is_private:
        raise HTTPException(
            status_code=422, detail="Cannot audit private profiles"
        )

    session = create_session()
    session.data["profile"] = result.profile.to_dict()
    session.data["posts"] = [p.to_dict() for p in result.posts]
    session.data["findings"] = []
    session.data["pipeline_outcomes"] = {}
    session.data["audit_started"] = False

    return FetchResponse(
        session_id=session.session_id,
        profile=result.profile.to_dict(),
        post_count=len(result.posts),
    )
