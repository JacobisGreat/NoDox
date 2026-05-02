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


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # Gemini (Google AI Studio) — replaced Anthropic after we ran out of
    # Anthropic credits during the hackathon. ``pro_model`` is the
    # reasoning-heavy model used by aggregator + query gen + geolocation
    # cluster pass; ``fast_model`` is the cheap model used for vision +
    # web triage + PII extraction.
    gemini_api_key: str
    gemini_api_url: str
    gemini_pro_model: str
    gemini_fast_model: str

    # Anthropic (preferred — when ANTHROPIC_API_KEY is set the audit
    # routes to Claude for every text + vision call instead of Gemini).
    anthropic_api_key: str

    # Serper.dev (web_footprint search backend; replaced Google CSE
    # after Google closed Custom Search JSON API to new accounts in 2026)
    serper_api_key: str

    # Per-audit budgets
    audit_cost_ceiling_usd: float
    web_footprint_budget_share_usd: float
    web_footprint_max_queries: int
    web_footprint_max_full_fetches: int

    # Gemini pricing ($ / million tokens)
    gemini_pro_input_cost_per_million: float
    gemini_pro_output_cost_per_million: float
    gemini_fast_input_cost_per_million: float
    gemini_fast_output_cost_per_million: float

    # App / session
    frontend_url: str
    session_cookie_name: str
    session_ttl_minutes: int

    # Web-footprint Workstream C extras
    hibp_api_key: str
    intelbase_api_key: str
    account_probe_enabled: bool
    account_probe_max_concurrent: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    anthropic_key = _env_str("ANTHROPIC_API_KEY")
    # When Anthropic is configured, the model-id fields used by the
    # call sites resolve to Claude IDs instead of Gemini IDs. Field
    # names stay "gemini_*" only to avoid touching every pipeline.
    if anthropic_key:
        pro_default = "claude-sonnet-4-6"
        fast_default = "claude-haiku-4-5-20251001"
    else:
        pro_default = "gemini-2.5-pro"
        fast_default = "gemini-2.5-flash"
    return Settings(
        gemini_api_key=_env_str("GEMINI_API_KEY"),
        gemini_api_url=_env_str(
            "GEMINI_API_URL", "https://generativelanguage.googleapis.com/v1beta"
        ),
        gemini_pro_model=_env_str("GEMINI_PRO_MODEL", pro_default),
        gemini_fast_model=_env_str("GEMINI_FAST_MODEL", fast_default),
        anthropic_api_key=anthropic_key,
        serper_api_key=_env_str("SERPER_API_KEY"),
        audit_cost_ceiling_usd=_env_float("AUDIT_COST_CEILING_USD", 1.0),
        web_footprint_budget_share_usd=_env_float(
            "WEB_FOOTPRINT_BUDGET_SHARE_USD", 0.35
        ),
        web_footprint_max_queries=_env_int("WEB_FOOTPRINT_MAX_QUERIES", 8),
        web_footprint_max_full_fetches=_env_int("WEB_FOOTPRINT_MAX_FULL_FETCHES", 10),
        # Pricing defaults match Claude Sonnet 4.6 (pro) + Haiku 4.5 (fast)
        # per-million-token rates so the cost ceiling stays accurate after
        # the Anthropic swap.
        gemini_pro_input_cost_per_million=_env_float(
            "GEMINI_PRO_INPUT_COST_PER_MILLION", 3.0
        ),
        gemini_pro_output_cost_per_million=_env_float(
            "GEMINI_PRO_OUTPUT_COST_PER_MILLION", 15.0
        ),
        gemini_fast_input_cost_per_million=_env_float(
            "GEMINI_FAST_INPUT_COST_PER_MILLION", 1.0
        ),
        gemini_fast_output_cost_per_million=_env_float(
            "GEMINI_FAST_OUTPUT_COST_PER_MILLION", 5.0
        ),
        frontend_url=_env_str("FRONTEND_URL", "http://localhost:5173"),
        session_cookie_name=_env_str("SESSION_COOKIE_NAME", "shieldclaw_session"),
        session_ttl_minutes=_env_int("SESSION_TTL_MINUTES", 120),
        hibp_api_key=_env_str("HIBP_API_KEY"),
        intelbase_api_key=_env_str("INTELBASE_API_KEY"),
        account_probe_enabled=_env_bool("ACCOUNT_PROBE_ENABLED", True),
        account_probe_max_concurrent=_env_int("ACCOUNT_PROBE_MAX_CONCURRENT", 8),
    )
