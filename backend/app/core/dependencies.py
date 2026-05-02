from functools import lru_cache

from app.core.config import get_settings
from app.core.session_store import EphemeralSessionStore
from app.services.meta_oauth import MetaOAuthService


@lru_cache
def get_session_store() -> EphemeralSessionStore:
    settings = get_settings()
    return EphemeralSessionStore(ttl_minutes=settings.session_ttl_minutes)


@lru_cache
def get_meta_oauth_service() -> MetaOAuthService:
    return MetaOAuthService(get_settings())

