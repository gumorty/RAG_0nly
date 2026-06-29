"""Background document ingestion monitor for RAGFlow.

Replaces the synchronous ``wait_for_documents`` polling with an
asynchronous background task so the HTTP endpoint returns immediately
and the document status gets updated as RAGFlow completes parsing.
"""

import logging
import threading
import time
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.entities import Collection, Document, DocumentStatus
from app.ragflow.client import RagFlowClient
from app.services.chunk_cache import sync_ready_document_chunks

logger = logging.getLogger(__name__)


def monitor_ragflow_parsing(
    document_id: str,
    dataset_id: str,
    ragflow_doc_id: str,
    collection_id: str | None = None,
) -> None:
    """Background thread target: poll RAGFlow until the document parse finishes.

    Called from ``_ingest_with_ragflow_or_local`` after uploading and triggering
    parsing.  Runs in a daemon thread so it never blocks the HTTP response.
    """
    client = RagFlowClient()
    deadline = time.time() + get_settings().ragflow_parse_timeout_seconds
    logger.info(
        "Background monitor started for doc %s (ragflow_doc %s) on dataset %s, timeout=%.0fs",
        document_id,
        ragflow_doc_id,
        dataset_id,
        get_settings().ragflow_parse_timeout_seconds,
    )

    while time.time() < deadline:
        time.sleep(5)
        try:
            docs = client.list_documents(dataset_id)
        except Exception:
            logger.warning("Transient error polling ragflow parsing for doc %s", document_id, exc_info=True)
            continue

        remote = None
        for doc in docs:
            if doc.get("id") == ragflow_doc_id:
                remote = doc
                break

        if remote is None:
            logger.warning("RAGFlow doc %s disappeared from dataset %s, marking failed", ragflow_doc_id, dataset_id)
            _update_status(document_id, DocumentStatus.failed, "RAGFlow document record not found")
            return

        run = str(remote.get("run") or "").upper()

        if run == "DONE":
            logger.info("RAGFlow parsing DONE for doc %s", document_id)
            _update_status(document_id, DocumentStatus.ready, None, remote)
            return

        if run in ("FAIL", "FAILED"):
            msg = str(remote.get("progress_msg") or "RAGFlow parsing failed")
            logger.warning("RAGFlow parsing FAILED for doc %s: %s", document_id, msg)
            _update_status(document_id, DocumentStatus.failed, msg, remote)
            return

        # Still RUNNING / UNSTART — keep the status up to date so the UI
        # reflects the latest progress even before completion.
        _apply_analysis(document_id, remote)

    # Timeout
    logger.warning("RAGFlow parsing TIMEOUT for doc %s after %.0fs", document_id, get_settings().ragflow_parse_timeout_seconds)
    _update_status(document_id, DocumentStatus.failed, "RAGFlow parsing timed out")


def sync_collection_document_statuses(collection_id: str | None = None) -> None:
    """Pull the latest document statuses from RAGFlow for one (or all) collections.

    This is safe to call periodically from the background sync loop.
    """
    with SessionLocal() as db:
        stmt = select(Collection)
        if collection_id:
            stmt = stmt.where(Collection.id == collection_id)
        collections = db.scalars(stmt).all()

    client = RagFlowClient()
    for collection in collections:
        dataset_id = (collection.metadata_ or {}).get("ragflow_dataset_id")
        if not dataset_id:
            continue
        try:
            remote_docs = client.list_documents(str(dataset_id))
        except Exception:
            logger.warning("sync: failed to list documents for dataset %s", dataset_id, exc_info=True)
            continue

        by_id = {str(item.get("id")): item for item in remote_docs}
        changed = False
        with SessionLocal() as db:
            documents = db.scalars(
                select(Document).where(Document.collection_id == collection.id)
            ).all()
            for document in documents:
                ragflow_doc_id = (document.metadata_ or {}).get("ragflow_document_id")
                remote = by_id.get(str(ragflow_doc_id))
                if remote:
                    _apply_ragflow_status(document, remote)
                    if document.status == DocumentStatus.ready:
                        try:
                            synced = sync_ready_document_chunks(db, document)
                            document.metadata_ = {
                                **(document.metadata_ or {}),
                                "chunk_cache_count": synced,
                            }
                        except Exception:
                            logger.warning("sync: failed to sync chunk cache for document %s", document.id, exc_info=True)
                    changed = True
            if changed:
                db.commit()


# ------------------------------------------------------------------
# Internal helpers (reuse patterns from routes.py)
# ------------------------------------------------------------------


def _update_status(
    document_id: str,
    status: DocumentStatus,
    error_message: str | None,
    remote: dict | None = None,
) -> None:
    with SessionLocal() as db:
        document = db.scalar(select(Document).where(Document.id == document_id))
        if not document:
            return
        document.status = status
        document.error_message = error_message
        if remote:
            _apply_ragflow_status(document, remote)
        if status == DocumentStatus.ready:
            try:
                synced = sync_ready_document_chunks(db, document)
                document.metadata_ = {
                    **(document.metadata_ or {}),
                    "chunk_cache_count": synced,
                }
            except Exception:
                logger.warning("failed to sync chunk cache for document %s", document.id, exc_info=True)
        db.commit()


def _apply_ragflow_status(document: Document, remote: dict) -> None:
    """Copy RAGFlow remote document metadata into the local Document row."""
    from app.api.routes import _apply_ragflow_document_status

    _apply_ragflow_document_status(document, remote)


def _apply_analysis(document_id: str, remote: dict) -> None:
    """Update analysis metadata (chunk_count, progress) without changing status."""
    from app.api.routes import _apply_ragflow_document_status

    with SessionLocal() as db:
        document = db.scalar(select(Document).where(Document.id == document_id))
        if not document:
            return
        _apply_ragflow_document_status(document, remote)
        db.commit()


def start_background_monitor(
    document_id: str,
    dataset_id: str,
    ragflow_doc_id: str,
) -> None:
    """Start a daemon thread that monitors RAGFlow parsing progress."""
    thread = threading.Thread(
        target=monitor_ragflow_parsing,
        args=(document_id, dataset_id, ragflow_doc_id),
        daemon=True,
        name=f"ragflow-monitor-{document_id[:8]}",
    )
    thread.start()
