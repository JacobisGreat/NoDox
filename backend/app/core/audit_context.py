from __future__ import annotations

import asyncio

from app.core.session_store import SessionState

BUDGET_CEILING = 1.00
PIPELINE_NAMES: frozenset[str] = frozenset({"identity", "geolocation", "web_footprint"})
DONE_STATUSES: frozenset[str] = frozenset({"complete", "error", "budget_exceeded"})

# Shared across the entire process — caps concurrent Anthropic vision calls
ANTHROPIC_SEMAPHORE = asyncio.Semaphore(5)

# Google Custom Search: enforced per-audit via AuditContext, not a global semaphore
GOOGLE_CSE_MAX_QUERIES = 15
GOOGLE_CSE_RATE_LIMIT_SECONDS = 1.0


async def add_cost(session: SessionState, amount: float) -> tuple[float, bool]:
    """Atomically add cost. Returns (new_total, over_budget)."""
    async with session.state_lock:
        current: float = session.data.get("audit_cost", 0.0)
        new_total = current + amount
        session.data["audit_cost"] = new_total
        return new_total, new_total >= BUDGET_CEILING


async def get_cost(session: SessionState) -> float:
    async with session.state_lock:
        return session.data.get("audit_cost", 0.0)


async def set_pipeline_status(session: SessionState, pipeline: str, status: str) -> None:
    async with session.state_lock:
        session.data.setdefault("pipeline_statuses", {})[pipeline] = status


async def all_pipelines_done(session: SessionState) -> bool:
    async with session.state_lock:
        statuses: dict[str, str] = session.data.get("pipeline_statuses", {})
        return all(statuses.get(p, "idle") in DONE_STATUSES for p in PIPELINE_NAMES)


async def accumulate_finding(session: SessionState, finding: dict) -> None:
    async with session.state_lock:
        session.data.setdefault("all_findings", []).append(finding)
