from fastapi import APIRouter, Depends, Request, Response

from app.core.config import Settings, get_settings
from app.core.dependencies import get_session_store
from app.core.session_store import EphemeralSessionStore
from app.schemas.auth import SessionView

router = APIRouter(tags=["session"])


def _cookie_kwargs(settings: Settings) -> dict[str, object]:
    return {
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": "lax",
        "max_age": settings.session_ttl_minutes * 60,
        "path": "/",
    }


@router.get("/session", response_model=SessionView)
async def get_session(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    store: EphemeralSessionStore = Depends(get_session_store),
) -> SessionView:
    session_cookie = request.cookies.get(settings.session_cookie_name)
    session = await store.get_or_create(session_cookie)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session.session_id,
        **_cookie_kwargs(settings),
    )
    return SessionView(session_id=session.session_id)
