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
    meta_redirect_uri: AnyHttpUrl = Field(default="http://localhost:8000/api/auth/meta/callback")
    meta_scope: str = "instagram_graph_user_profile,instagram_graph_user_media"
    meta_auth_url: AnyHttpUrl = Field(default="https://api.instagram.com/oauth/authorize")
    meta_token_url: AnyHttpUrl = Field(default="https://api.instagram.com/oauth/access_token")
    meta_graph_base_url: AnyHttpUrl = Field(default="https://graph.instagram.com")
    meta_exchange_long_lived_token: bool = False
    meta_long_lived_token_url: AnyHttpUrl = Field(default="https://graph.instagram.com/access_token")
    instagram_graph_api_base: AnyHttpUrl = Field(default="https://graph.instagram.com")

    anthropic_api_key: str | None = None
    anthropic_api_url: AnyHttpUrl = Field(default="https://api.anthropic.com/v1/messages")
    anthropic_version: str = "2023-06-01"
    anthropic_haiku_model: str = "claude-haiku-4-5-20251001"
    anthropic_sonnet_model: str = "claude-sonnet-4-6"

    google_cse_api_key: str | None = None
    google_cse_cx: str | None = None
    google_cse_api_url: AnyHttpUrl = Field(default="https://www.googleapis.com/customsearch/v1")

    audit_cost_ceiling_usd: float = 1.0
    web_footprint_budget_share_usd: float = 0.35
    web_footprint_max_queries: int = 15
    web_footprint_max_full_fetches: int = 20
    web_footprint_queries_per_call: int = 5

    claude_sonnet_input_cost_per_million: float = 3.0
    claude_sonnet_output_cost_per_million: float = 15.0
    claude_haiku_input_cost_per_million: float = 1.0
    claude_haiku_output_cost_per_million: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
