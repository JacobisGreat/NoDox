from __future__ import annotations

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse

from app.core.config import Settings, get_settings
from app.core.dependencies import get_meta_oauth_service, get_session_store
from app.core.session_store import EphemeralSessionStore
from app.schemas.auth import OAuthStartResponse, SessionView
from app.services.meta_oauth import MetaOAuthService, OAuthServiceError

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_kwargs(settings: Settings) -> dict[str, object]:
    return {
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": "lax",
        "max_age": settings.session_ttl_minutes * 60,
        "path": "/",
    }


def _frontend_callback_url(settings: Settings, status_label: str, message: str | None = None) -> str:
    base_url = str(settings.frontend_url).rstrip("/")
    query = {"status": status_label}
    if message:
        query["message"] = message
    return f"{base_url}/oauth/callback?{urlencode(query)}"


@router.get("/meta/start", response_model=OAuthStartResponse)
async def meta_oauth_start(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    store: EphemeralSessionStore = Depends(get_session_store),
    oauth_service: MetaOAuthService = Depends(get_meta_oauth_service),
) -> OAuthStartResponse:
    session_cookie = request.cookies.get(settings.session_cookie_name)
    session = await store.get_or_create(session_cookie)
    session.auth_status = "auth_in_progress"
    session.gate_passed = False
    session.last_error = None
    session.oauth_state = secrets.token_urlsafe(32)
    await store.save(session)

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session.session_id,
        **_cookie_kwargs(settings),
    )
    return OAuthStartResponse(
        auth_url=oauth_service.build_authorize_url(session.oauth_state),
        session_id=session.session_id,
    )


@router.get("/meta/callback")
async def meta_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_reason: str | None = None,
    settings: Settings = Depends(get_settings),
    store: EphemeralSessionStore = Depends(get_session_store),
    oauth_service: MetaOAuthService = Depends(get_meta_oauth_service),
) -> RedirectResponse:
    redirect_error = _frontend_callback_url(settings, "error")

    session_cookie = request.cookies.get(settings.session_cookie_name)
    session = await store.get(session_cookie)
    if not session:
        return RedirectResponse(
            _frontend_callback_url(settings, "error", "Session expired before OAuth callback."),
            status_code=status.HTTP_302_FOUND,
        )

    if error:
        session.auth_status = "error"
        session.gate_passed = False
        session.last_error = error_reason or error
        await store.save(session)
        response = RedirectResponse(
            _frontend_callback_url(settings, "error", session.last_error),
            status_code=status.HTTP_302_FOUND,
        )
        response.set_cookie(
            key=settings.session_cookie_name,
            value=session.session_id,
            **_cookie_kwargs(settings),
        )
        return response

    if not code or not state or session.oauth_state != state:
        session.auth_status = "error"
        session.gate_passed = False
        session.last_error = "OAuth state validation failed."
        await store.save(session)
        response = RedirectResponse(redirect_error, status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            key=settings.session_cookie_name,
            value=session.session_id,
            **_cookie_kwargs(settings),
        )
        return response

    try:
        token_data = await oauth_service.exchange_code(code)
        access_token = token_data["access_token"]
        me_data = await oauth_service.fetch_me(access_token)
        media_data = await oauth_service.fetch_me_media(access_token)
    except OAuthServiceError as exc:
        session.auth_status = "error"
        session.gate_passed = False
        session.last_error = str(exc)
        await store.save(session)
        response = RedirectResponse(
            _frontend_callback_url(settings, "error", "OAuth succeeded but /me/media gate failed."),
            status_code=status.HTTP_302_FOUND,
        )
        response.set_cookie(
            key=settings.session_cookie_name,
            value=session.session_id,
            **_cookie_kwargs(settings),
        )
        return response

    session.access_token = access_token
    session.token_expires_at = oauth_service.extract_expiry(token_data)
    session.ig_user = me_data
    session.media = media_data
    session.auth_status = "authenticated"
    session.gate_passed = True
    session.last_error = None
    session.oauth_state = None
    await store.save(session)

    response = RedirectResponse(
        _frontend_callback_url(settings, "success"),
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session.session_id,
        **_cookie_kwargs(settings),
    )
    return response


@router.post("/logout", response_model=SessionView)
async def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    store: EphemeralSessionStore = Depends(get_session_store),
) -> SessionView:
    session_cookie = request.cookies.get(settings.session_cookie_name)
    session = await store.get_or_create(session_cookie)
    await store.clear(session.session_id)
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
    )
    fresh = await store.get_or_create()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=fresh.session_id,
        **_cookie_kwargs(settings),
    )
    return SessionView(
        session_id=fresh.session_id,
        auth_status=fresh.auth_status,
        gate_passed=fresh.gate_passed,
        ig_user=fresh.ig_user,
        media_count=len(fresh.media),
        last_error=fresh.last_error,
    )
