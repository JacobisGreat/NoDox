from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.config import Settings, get_settings
from app.core.dependencies import get_session_store
from app.core.session_store import EphemeralSessionStore
from app.schemas.auth import MediaGateResponse, SessionView

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
    return SessionView(
        session_id=session.session_id,
        auth_status=session.auth_status,
        gate_passed=session.gate_passed,
        ig_user=session.ig_user,
        media_count=len(session.media),
        last_error=session.last_error,
    )


@router.get("/me/media", response_model=MediaGateResponse)
async def get_me_media(
    request: Request,
    settings: Settings = Depends(get_settings),
    store: EphemeralSessionStore = Depends(get_session_store),
) -> MediaGateResponse:
    session_cookie = request.cookies.get(settings.session_cookie_name)
    session = await store.get(session_cookie)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active session. Complete OAuth first.",
        )
    if not session.gate_passed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OAuth gate not satisfied. /me/media has not completed successfully.",
        )
    return MediaGateResponse(gate_passed=True, media=session.media)

