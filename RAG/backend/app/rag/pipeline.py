import uuid
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Chunk, Document, DocumentStatus
from app.rag.chunking import HierarchicalChunker
from app.rag.analysis import build_document_analysis
from app.rag.embeddings import get_embedding_client
from app.rag.normalize import sha256_text, tokenize_for_sparse
from app.rag.parsers import DocumentParser
from app.rag.vector_store import VectorStore
from app.services.storage import ObjectStorage


class IngestionPipeline:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.storage = ObjectStorage()
        self.parser = DocumentParser()
        self.chunker = HierarchicalChunker(self.settings.chunk_max_tokens, self.settings.chunk_overlap_tokens)
        self.embedding = get_embedding_client()
        self.vector_store = VectorStore()

    def run(self, document_id: str) -> None:
        document = self.db.scalar(select(Document).where(Document.id == document_id))
        if not document:
            raise ValueError(f"Document not found: {document_id}")
        local_path = Path("data/cache") / f"{document.id}-{document.filename}"
        try:
            document.status = DocumentStatus.parsing
            document.error_message = None
            self.db.commit()

            self.storage.download_to_path(document.object_key, local_path)
            parsed = self.parser.parse(local_path, document.filename, document.content_type)
            document.title = parsed.title or document.title
            document.metadata_ = {**(document.metadata_ or {}), "parsed": parsed.metadata}

            document.status = DocumentStatus.indexing
            self.db.execute(delete(Chunk).where(Chunk.document_id == document.id))
            self.db.commit()

            candidates = self.chunker.chunk(parsed)
            if not candidates:
                raise ValueError("No text chunks were extracted from this document.")
            analysis = build_document_analysis(parsed, candidates)
            document.metadata_ = {**(document.metadata_ or {}), "analysis": analysis}
            embeddings = self.embedding.embed_documents([candidate.normalized_content for candidate in candidates])
            vector_size = len(embeddings[0])

            points = []
            for ordinal, (candidate, vector) in enumerate(zip(candidates, embeddings, strict=True), start=1):
                chunk = Chunk(
                    id=str(uuid.uuid4()),
                    document_id=document.id,
                    collection_id=document.collection_id,
                    ordinal=ordinal,
                    title_path=candidate.title_path,
                    content=candidate.content,
                    normalized_content=candidate.normalized_content,
                    token_count=candidate.token_count,
                    content_hash=sha256_text(candidate.normalized_content),
                    vector_id=None,
                    sparse_terms=tokenize_for_sparse(candidate.normalized_content),
                    metadata_=candidate.metadata,
                )
                chunk.vector_id = chunk.id
                self.db.add(chunk)
                points.append(
                    {
                        "id": chunk.id,
                        "vector": vector,
                        "payload": {
                            "collection_id": document.collection_id,
                            "document_id": document.id,
                            "chunk_id": chunk.id,
                            "title": document.title,
                            "filename": document.filename,
                            "acl": document.acl or ["public"],
                            "project": document.project,
                            "meeting_date": document.meeting_date,
                            "title_path": candidate.title_path,
                        },
                    }
                )
            self.vector_store.upsert_chunks(points, vector_size=vector_size)
            document.status = DocumentStatus.ready
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            document = self.db.scalar(select(Document).where(Document.id == document_id))
            if not document:
                raise
            document.status = DocumentStatus.failed
            document.error_message = str(exc)
            self.db.commit()
            raise
        finally:
            if local_path.exists():
                local_path.unlink()
