"""FastAPI application entrypoint.

On startup it makes the system self-bootstrapping: apply DB migrations and ensure
the Qdrant collection + payload indexes exist (idempotent), so a fresh `docker
compose up` comes up ready. The API itself is stateless and holds no model — it
calls the TEI service for query embeddings.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from sherpa.api.routes import documents, health, search
from sherpa.clients.qdrant import ensure_collection

log = logging.getLogger("sherpa.api")

# Project root holds alembic.ini (cwd in the container is the project root).
_ALEMBIC_INI = Path(os.getenv("ALEMBIC_INI", "alembic.ini"))


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    command.upgrade(cfg, "head")
    log.info("migrations applied (head)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Best-effort bootstrap; can be disabled (e.g. when a dedicated init job runs).
    if os.getenv("RUN_MIGRATIONS_ON_STARTUP", "1") == "1":
        _run_migrations()
    if os.getenv("BOOTSTRAP_QDRANT_ON_STARTUP", "1") == "1":
        ensure_collection()
        log.info("qdrant collection + payload indexes ready")
    yield


app = FastAPI(
    title="Sherpa — PDF semantic search",
    version="0.1.0",
    summary="Self-hosted, retrieval-only semantic search over private PDFs.",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(documents.router)
app.include_router(search.router)
