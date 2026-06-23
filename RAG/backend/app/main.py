import asyncio
import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.core.db import init_db
from app.workers.ragflow_ingestion import sync_collection_document_statuses

logger = logging.getLogger(__name__)


async def periodic_ragflow_sync() -> None:
    """Periodically sync RAGFlow document statuses to our PostgreSQL.

    Runs every ``ragflow_sync_interval_seconds`` and is cancelled on shutdown.
    """
    interval = get_settings().ragflow_sync_interval_seconds
    logger.info("Periodic RAGFlow sync started (interval=%ds)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            sync_collection_document_statuses()
            logger.debug("RAGFlow sync completed")
        except Exception:
            logger.exception("RAGFlow periodic sync failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI application lifespan — init DB then start background sync."""
    init_db()
    if get_settings().ragflow_enabled:
        try:
            sync_collection_document_statuses()
            logger.info("Startup RAGFlow sync completed")
        except Exception:
            logger.warning("Startup RAGFlow sync failed (service may not be ready yet)")
        task = asyncio.create_task(periodic_ragflow_sync())
    yield
    if get_settings().ragflow_enabled:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Enterprise RAG Knowledge Base", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
