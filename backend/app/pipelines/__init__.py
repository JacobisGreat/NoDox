"""Async intelligence pipelines.

Each pipeline exposes ``async def run(session: SessionState) -> None`` and
publishes events directly via ``session.publish_event``. Failure must be
caught inside the pipeline — exceptions escaping ``run`` will only be
logged by the coordinator.
"""
