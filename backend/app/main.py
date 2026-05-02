"""FastAPI entrypoint for NODOXX."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes.audit import router as audit_router
from app.routes.profile import router as profile_router
from app.routes.stream import router as stream_router
from app.session_store import SESSIONS, gc_loop

logger = logging.getLogger("nodoxx")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    gc_task = asyncio.create_task(
        gc_loop(ttl_minutes=settings.session_ttl_minutes), name="session-gc"
    )

    # Background-warm geoclip so the first audit doesn't pay the 30-90s
    # cold start. Imported lazily because torch/geoclip may not be
    # installed in dev / minimal environments.
    async def _warm() -> None:
        try:
            from app.pipelines.geolocation import warm_geoclip

            await warm_geoclip()
        except Exception as exc:
            logger.info("geoclip warm-up skipped: %s", exc)
        try:
            from app.services.geocode_text import warmup as warm_geocoder

            await warm_geocoder()
        except Exception as exc:
            logger.info("geocode_text warm-up skipped: %s", exc)

    warm_task = asyncio.create_task(_warm(), name="geoclip-warm")

    try:
        yield
    finally:
        gc_task.cancel()
        warm_task.cancel()
        try:
            await warm_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await gc_task
        except asyncio.CancelledError:
            pass
        # Best-effort cleanup of any remaining session http clients.
        for sid in list(SESSIONS.keys()):
            state = SESSIONS.pop(sid, None)
            if state is None:
                continue
            for task in state.audit_tasks.values():
                if not task.done():
                    task.cancel()
            http = state.data.get("http")
            if http is not None:
                try:
                    await http.aclose()
                except Exception:
                    pass


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="NODOXX", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url.rstrip("/")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(profile_router, prefix="/api")
    app.include_router(audit_router, prefix="/api")
    app.include_router(stream_router, prefix="/api")

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
