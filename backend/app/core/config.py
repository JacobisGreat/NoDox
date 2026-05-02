from functools import lru_cache

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "NoDox API"
    environment: str = "development"

    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    frontend_url: AnyHttpUrl = Field(default="http://localhost:5173")

    session_cookie_name: str = "nodox_session"
    session_ttl_minutes: int = 120
    session_cookie_secure: bool = False

    meta_client_id: str = ""
    meta_client_secret: str = ""
    meta_redirect_uri: AnyHttpUrl = Field(
        default="http://localhost:8000/api/auth/meta/callback"
    )
    meta_scope: str = "instagram_graph_user_profile,instagram_graph_user_media"

    meta_auth_url: AnyHttpUrl = Field(default="https://api.instagram.com/oauth/authorize")
    meta_token_url: AnyHttpUrl = Field(
        default="https://api.instagram.com/oauth/access_token"
    )
    meta_graph_base_url: AnyHttpUrl = Field(default="https://graph.instagram.com")
    meta_exchange_long_lived_token: bool = False
    meta_long_lived_token_url: AnyHttpUrl = Field(
        default="https://graph.instagram.com/access_token"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

