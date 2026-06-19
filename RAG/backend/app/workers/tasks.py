from app.core.db import SessionLocal
from app.rag.pipeline import IngestionPipeline
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.ingest_document")
def ingest_document(document_id: str) -> None:
    db = SessionLocal()
    try:
        IngestionPipeline(db).run(document_id)
    finally:
        db.close()
