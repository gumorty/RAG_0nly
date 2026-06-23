"""Background migration: backfill RAGFlow dataset & chat IDs for legacy collections.

Run via::

    docker exec rag-api-1 python -m app.workers.migrate_ragflow_ids

This is safe to run multiple times (idempotent).  It creates RAGFlow datasets
and chats for collections that are missing them, then re-uploads any documents
that don't have a `ragflow_document_id`.
"""

import logging
import sys
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, init_db
from app.models.entities import Collection, Document, DocumentStatus
from app.ragflow.client import RagFlowClient, RagFlowError
from app.ragflow.chunk_method import select_chunk_method
from app.workers.ragflow_ingestion import start_background_monitor

logger = logging.getLogger(__name__)


def _build_system_prompt():
    return (
        "You are an enterprise RAG knowledge-base assistant. "
        "Answer strictly from the retrieved evidence. "
        "Do not invent facts. "
        "Add citation markers after key conclusions using the format [1]. "
        "If evidence is insufficient, state exactly what is missing."
    )


def migrate_collections():
    """Backfill missing ragflow_dataset_id and ragflow_chat_id for all collections."""
    settings = get_settings()
    if not settings.ragflow_enabled:
        logger.warning("RAGFlow is not enabled, skipping migration")
        return

    client = RagFlowClient()
    init_db()
    with SessionLocal() as db:
        collections = db.scalars(select(Collection)).all()

    for collection in collections:
        meta = dict(collection.metadata_ or {})
        dataset_id = meta.get("ragflow_dataset_id")
        chat_id = meta.get("ragflow_chat_id")
        changed = False

        # Step 1: Create or verify dataset
        if not dataset_id:
            try:
                dataset = client.create_dataset(
                    name=collection.name,
                    description=collection.description or "",
                )
                dataset_id = dataset.get("id")
                meta["ragflow_dataset_id"] = dataset_id
                meta["ragflow_dataset_name"] = dataset.get("name")
                logger.info("Created dataset %s for collection '%s'", dataset_id, collection.name)
                changed = True
            except RagFlowError as exc:
                logger.error("Failed to create dataset for '%s': %s", collection.name, exc)
                continue

        # Step 2: Create chat (dialog) if chat completions enabled
        if settings.ragflow_enable_chat_completions and not chat_id and dataset_id:
            try:
                chat = client.create_chat(
                    name=f"{collection.name}-chat",
                    dataset_ids=[dataset_id],
                    llm_id=settings.ragflow_chat_model,
                    top_n=6,
                    similarity_threshold=0.1,
                    vector_similarity_weight=0.3,
                    prompt_config={
                        "system": _build_system_prompt(),
                        "quote": True,
                        "refine_multiturn": True,
                    },
                )
                meta["ragflow_chat_id"] = chat.get("id")
                meta["ragflow_chat_name"] = chat.get("name")
                logger.info("Created chat %s for collection '%s'", chat.get("id"), collection.name)
                changed = True
            except RagFlowError as exc:
                logger.error("Failed to create chat for '%s': %s", collection.name, exc)

        if changed:
            with SessionLocal() as db:
                c = db.scalar(select(Collection).where(Collection.id == collection.id))
                if c:
                    c.metadata_ = meta
                    db.commit()
                    logger.info("Updated metadata for collection '%s'", collection.name)

    logger.info("Collection migration complete")


def migrate_documents():
    """Re-upload documents that lack a ragflow_document_id."""
    settings = get_settings()
    if not settings.ragflow_enabled:
        return

    client = RagFlowClient()
    from app.services.storage import ObjectStorage

    with SessionLocal() as db:
        collections = {c.id: c for c in db.scalars(select(Collection))}
        documents = db.scalars(select(Document).order_by(Document.created_at)).all()

    for doc in documents:
        meta = dict(doc.metadata_ or {})
        if meta.get("ragflow_document_id"):
            continue  # already has RAGFlow doc
        collection = collections.get(doc.collection_id)
        if not collection:
            continue
        dataset_id = (collection.metadata_ or {}).get("ragflow_dataset_id")
        if not dataset_id:
            continue

        logger.info("Re-uploading doc '%s' (id=%s) to dataset %s", doc.filename, doc.id, dataset_id)
        try:
            data = ObjectStorage().get_bytes(doc.object_key)
        except Exception as exc:
            logger.warning("Cannot read object %s: %s", doc.object_key, exc)
            continue

        try:
            uploaded = client.upload_document(dataset_id, doc.filename, data, doc.content_type)
            ragflow_doc_id = str(uploaded.get("id"))
            meta["ragflow_dataset_id"] = str(dataset_id)
            meta["ragflow_document_id"] = ragflow_doc_id
            meta["ragflow_document"] = uploaded

            # Override chunk method per document type
            chunk_method = select_chunk_method(doc.filename, doc.content_type)
            client.update_document_parser(dataset_id, ragflow_doc_id, chunk_method=chunk_method)

            client.parse_documents(dataset_id, [ragflow_doc_id])
            start_background_monitor(doc.id, str(dataset_id), ragflow_doc_id)

            with SessionLocal() as db:
                d = db.scalar(select(Document).where(Document.id == doc.id))
                if d:
                    d.metadata_ = meta
                    d.status = DocumentStatus.parsing
                    d.error_message = None
                    db.commit()
                    logger.info("  -> uploaded & parsing started (ragflow_doc=%s)", ragflow_doc_id)
        except Exception as exc:
            logger.error("  -> upload failed: %s", exc)

    logger.info("Document migration complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if "--documents" in sys.argv:
        migrate_documents()
    else:
        migrate_collections()
        migrate_documents()
