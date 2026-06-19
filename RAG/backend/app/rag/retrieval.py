import math
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Chunk, Document
from app.rag.embeddings import get_embedding_client
from app.rag.normalize import tokenize_for_sparse
from app.rag.reranker import get_reranker
from app.rag.schemas import RetrievedChunk, RetrievalStrategy
from app.rag.vector_store import VectorStore


class RetrievalService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.vector_store = VectorStore()
        self.embedding = get_embedding_client()
        self.reranker = get_reranker()

    def retrieve(
        self,
        collection_id: str,
        query: str,
        strategy: RetrievalStrategy,
        acl_principals: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        query_vector = self.embedding.embed_query(query)
        principals = acl_principals or ["public"]
        dense_hits = self.vector_store.search(query_vector, collection_id, strategy.dense_top_k, acl_principals=principals)
        keyword_hits = self._keyword_search(collection_id, query, strategy.keyword_top_k, acl_principals=principals)

        fused = self._fuse_hits(dense_hits, keyword_hits)
        chunks = self._load_chunks(fused)
        reranked = self.reranker.rerank(query, chunks)
        selected = reranked[: strategy.final_top_k]
        if strategy.use_parent_context and strategy.context_window_chunks > 0:
            return self._expand_context(selected, strategy.context_window_chunks)
        return selected

    def _keyword_search(self, collection_id: str, query: str, top_k: int, acl_principals: list[str]) -> list[dict]:
        terms = list(tokenize_for_sparse(query).keys())
        if not terms:
            return []
        stmt = (
            select(Chunk, Document)
            .join(Document, Chunk.document_id == Document.id)
            .where(Chunk.collection_id == collection_id)
            .limit(500)
        )
        scored: list[dict] = []
        for chunk, document in self.db.execute(stmt).all():
            if not _document_allowed(document.acl or [], acl_principals):
                continue
            text = chunk.normalized_content.lower()
            score = 0.0
            for term in terms:
                count = text.count(term)
                if count:
                    score += 1.0 + math.log(count + 1)
            if score > 0:
                scored.append({"chunk_id": chunk.id, "score": score})
        scored.sort(key=lambda item: item["score"], reverse=True)
        max_score = scored[0]["score"] if scored else 1.0
        return [{"chunk_id": item["chunk_id"], "score": item["score"] / max_score} for item in scored[:top_k]]

    def _fuse_hits(self, dense_hits: list[dict], keyword_hits: list[dict]) -> dict[str, dict]:
        fused: dict[str, dict] = defaultdict(lambda: {"dense_score": 0.0, "keyword_score": 0.0, "score": 0.0})
        for rank, hit in enumerate(dense_hits, start=1):
            chunk_id = hit["chunk_id"]
            dense_score = float(hit["score"])
            fused[chunk_id]["dense_score"] = max(fused[chunk_id]["dense_score"], dense_score)
            fused[chunk_id]["score"] += 1.0 / (60 + rank)
        for rank, hit in enumerate(keyword_hits, start=1):
            chunk_id = hit["chunk_id"]
            keyword_score = float(hit["score"])
            fused[chunk_id]["keyword_score"] = max(fused[chunk_id]["keyword_score"], keyword_score)
            fused[chunk_id]["score"] += 1.0 / (60 + rank)
        return fused

    def _load_chunks(self, fused: dict[str, dict]) -> list[RetrievedChunk]:
        if not fused:
            return []
        stmt = (
            select(Chunk, Document)
            .join(Document, Chunk.document_id == Document.id)
            .where(Chunk.id.in_(list(fused.keys())))
        )
        chunks: list[RetrievedChunk] = []
        for chunk, document in self.db.execute(stmt).all():
            scores = fused[chunk.id]
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    title=document.title,
                    content=chunk.content,
                    title_path=chunk.title_path,
                    source_uri=document.source_uri,
                    score=float(scores["score"]),
                    dense_score=float(scores["dense_score"]),
                    keyword_score=float(scores["keyword_score"]),
                    metadata={
                        "filename": document.filename,
                        "author": document.author,
                        "project": document.project,
                        "meeting_date": document.meeting_date,
                        **(chunk.metadata_ or {}),
                    },
                )
            )
        return sorted(chunks, key=lambda item: item.score, reverse=True)

    def _expand_context(self, chunks: list[RetrievedChunk], window: int) -> list[RetrievedChunk]:
        if not chunks:
            return chunks
        source_chunks = {
            chunk.id: chunk
            for chunk in self.db.scalars(select(Chunk).where(Chunk.id.in_([item.chunk_id for item in chunks]))).all()
        }
        expanded: list[RetrievedChunk] = []
        for retrieved in chunks:
            source = source_chunks.get(retrieved.chunk_id)
            if not source:
                expanded.append(retrieved)
                continue
            neighbors = self.db.scalars(
                select(Chunk)
                .where(
                    Chunk.document_id == source.document_id,
                    Chunk.ordinal >= source.ordinal - window,
                    Chunk.ordinal <= source.ordinal + window,
                )
                .order_by(Chunk.ordinal.asc())
            ).all()
            if len(neighbors) <= 1:
                expanded.append(retrieved)
                continue
            retrieved.metadata = {
                **retrieved.metadata,
                "matched_chunk_id": retrieved.chunk_id,
                "expanded_chunk_ids": [neighbor.id for neighbor in neighbors],
                "context_expanded": True,
            }
            retrieved.content = "\n\n".join(f"[chunk {neighbor.ordinal}]\n{neighbor.content}" for neighbor in neighbors)
            expanded.append(retrieved)
        return expanded


def _document_allowed(acl: list[str], principals: list[str]) -> bool:
    if "admin" in principals or "maintainer" in principals:
        return True
    effective_acl = acl or ["public"]
    return "public" in effective_acl or any(principal in effective_acl for principal in principals)
