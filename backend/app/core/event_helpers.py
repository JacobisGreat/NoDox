from __future__ import annotations

from app.core.session_store import SessionState


async def emit_finding(session: SessionState, pipeline: str, finding: dict) -> None:
    await session.publish_event({"type": "finding", "pipeline": pipeline, "finding": finding})


async def emit_pipeline_status(session: SessionState, pipeline: str, status: str) -> None:
    await session.publish_event({"type": "pipeline_status", "pipeline": pipeline, "status": status})


async def emit_cost_update(session: SessionState, cost: float) -> None:
    await session.publish_event({"type": "cost_update", "cost": round(cost, 6)})


async def emit_aggregator_done(session: SessionState, result: dict) -> None:
    await session.publish_event({"type": "aggregator_done", "result": result})
    await session.publish_event({"type": "stream_done"})
