"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings)
    logger.info(
        "CoursePilot backend starting (env=%s, llm_provider=%s)",
        settings.coursepilot_env.value,
        settings.llm_provider,
    )
    # Note: no database connection is opened at startup. The app must boot
    # even when Postgres is down — /ready reports the dependency instead.
    # Engines are built lazily on first use, so this stays true with pooling.
    yield
    # Phase 5.7: return every pooled connection before the process exits.
    # Without this a bounded pool holds sockets open until the interpreter
    # dies, which is precisely why the sync engine used to refuse to pool.
    from app.db.session import dispose_engines_async

    await dispose_engines_async()
    logger.info("CoursePilot backend shutting down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="CoursePilot API",
        version="0.1.0",
        description=(
            "Rutgers degree-planning and schedule-optimization API. "
            "Academic correctness is determined by deterministic validation, "
            "not by model output."
        ),
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Phase 5.10: correlation id, request metrics and the error contract.
    # Installed after CORS so the id header survives the CORS response.
    from app.api import middleware

    middleware.install(app)

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
