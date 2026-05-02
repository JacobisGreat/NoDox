"""Environment-driven configuration for NODOXX.

Values are loaded once at import time via ``get_settings`` and cached.
Every module that needs config should call ``get_settings()`` rather than
reading ``os.environ`` directly so tests can monkeypatch a single object.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # python-dotenv is optional at runtime — env may already be set.
    pass


def _env_str(key: str, default: str = "") -> str:
    value = os.environ.get(key)
    return value if value is not None and value != "" else default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Anthropic
    anthropic_api_key: str
    anthropic_api_url: str
    anthropic_version: str
    anthropic_sonnet_model: str
    anthropic_haiku_model: str

    # Google CSE (used by instance 2's web_footprint pipeline)
    google_cse_api_key: str
    google_cse_cx: str

    # Per-audit budgets
    audit_cost_ceiling_usd: float
    web_footprint_budget_share_usd: float
    web_footprint_max_queries: int
    web_footprint_max_full_fetches: int

    # Anthropic pricing
    claude_sonnet_input_cost_per_million: float
    claude_sonnet_output_cost_per_million: float
    claude_haiku_input_cost_per_million: float
    claude_haiku_output_cost_per_million: float

    # App / session
    frontend_url: str
    session_cookie_name: str
    session_ttl_minutes: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        anthropic_api_key=_env_str("ANTHROPIC_API_KEY"),
        anthropic_api_url=_env_str(
            "ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"
        ),
        anthropic_version=_env_str("ANTHROPIC_VERSION", "2023-06-01"),
        anthropic_sonnet_model=_env_str("ANTHROPIC_SONNET_MODEL", "claude-sonnet-4-6"),
        anthropic_haiku_model=_env_str(
            "ANTHROPIC_HAIKU_MODEL", "claude-haiku-4-5-20251001"
        ),
        google_cse_api_key=_env_str("GOOGLE_CSE_API_KEY"),
        google_cse_cx=_env_str("GOOGLE_CSE_CX"),
        audit_cost_ceiling_usd=_env_float("AUDIT_COST_CEILING_USD", 1.0),
        web_footprint_budget_share_usd=_env_float(
            "WEB_FOOTPRINT_BUDGET_SHARE_USD", 0.35
        ),
        web_footprint_max_queries=_env_int("WEB_FOOTPRINT_MAX_QUERIES", 15),
        web_footprint_max_full_fetches=_env_int("WEB_FOOTPRINT_MAX_FULL_FETCHES", 20),
        claude_sonnet_input_cost_per_million=_env_float(
            "CLAUDE_SONNET_INPUT_COST_PER_MILLION", 3.0
        ),
        claude_sonnet_output_cost_per_million=_env_float(
            "CLAUDE_SONNET_OUTPUT_COST_PER_MILLION", 15.0
        ),
        claude_haiku_input_cost_per_million=_env_float(
            "CLAUDE_HAIKU_INPUT_COST_PER_MILLION", 1.0
        ),
        claude_haiku_output_cost_per_million=_env_float(
            "CLAUDE_HAIKU_OUTPUT_COST_PER_MILLION", 5.0
        ),
        frontend_url=_env_str("FRONTEND_URL", "http://localhost:5173"),
        session_cookie_name=_env_str("SESSION_COOKIE_NAME", "shieldclaw_session"),
        session_ttl_minutes=_env_int("SESSION_TTL_MINUTES", 120),
    )
