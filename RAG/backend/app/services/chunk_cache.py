from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Document, DomainLexiconTerm, RetrievalChunkCache
from app.rag.normalize import estimate_tokens, normalize_text, sha256_text, tokenize_for_sparse
from app.rag.schemas import RetrievedChunk
from app.ragflow.client import RagFlowClient


def sync_ready_document_chunks(db: Session, document: Document, *, page_size: int = 100) -> int:
    metadata = document.metadata_ or {}
    dataset_id = metadata.get("ragflow_dataset_id")
    ragflow_doc_id = metadata.get("ragflow_document_id")
    if not dataset_id or not ragflow_doc_id:
        return 0
    return sync_document_chunks(
        db,
        document=document,
        dataset_id=str(dataset_id),
        ragflow_document_id=str(ragflow_doc_id),
        page_size=page_size,
    )


def sync_document_chunks(
    db: Session,
    *,
    document: Document,
    dataset_id: str,
    ragflow_document_id: str,
    page_size: int = 100,
) -> int:
    client = RagFlowClient()
    synced = 0
    seen: set[str] = set()
    page = 1
    while True:
        payload = client.list_chunks(dataset_id, ragflow_document_id, page=page, page_size=page_size)
        raw_chunks = _extract_chunk_list(payload)
        if not raw_chunks:
            break
        for raw in raw_chunks:
            chunk_id = _chunk_id(raw)
            content = _readable_evidence_text(_chunk_content(raw))
            if not chunk_id or not content:
                continue
            seen.add(chunk_id)
            title = _chunk_title(raw, fallback=document.title)
            title_path = _chunk_title_path(raw, title)
            normalized = normalize_text(content)
            row = db.scalar(
                select(RetrievalChunkCache).where(
                    RetrievalChunkCache.collection_id == document.collection_id,
                    RetrievalChunkCache.ragflow_chunk_id == chunk_id,
                )
            )
            if not row:
                row = RetrievalChunkCache(
                    collection_id=document.collection_id,
                    document_id=document.id,
                    ragflow_dataset_id=dataset_id,
                    ragflow_document_id=ragflow_document_id,
                    ragflow_chunk_id=chunk_id,
                    content=content,
                    normalized_content=normalized,
                    content_hash=sha256_text(normalized),
                )
                db.add(row)
            row.document_id = document.id
            row.ragflow_dataset_id = dataset_id
            row.ragflow_document_id = ragflow_document_id
            row.title = title
            row.title_path = title_path
            row.page_no = _page_no(raw)
            row.content = content
            row.normalized_content = normalized
            row.content_hash = sha256_text(normalized)
            row.token_count = estimate_tokens(content)
            row.score_metadata = _score_metadata(raw)
            row.metadata_ = {"ragflow": _safe_metadata(raw)}
            row.available = True
            row.updated_at = datetime.utcnow()
            synced += 1
        if len(raw_chunks) < page_size:
            break
        page += 1

    if seen:
        db.query(RetrievalChunkCache).filter(
            RetrievalChunkCache.collection_id == document.collection_id,
            RetrievalChunkCache.ragflow_document_id == ragflow_document_id,
            ~RetrievalChunkCache.ragflow_chunk_id.in_(seen),
        ).update({RetrievalChunkCache.available: False}, synchronize_session=False)
    return synced


def sync_cache_from_retrieved_chunks(
    db: Session,
    *,
    collection_id: str,
    dataset_id: str | None,
    chunks: list[RetrievedChunk],
) -> int:
    synced = 0
    for chunk in chunks:
        chunk_id = chunk.chunk_id
        content = _readable_evidence_text(chunk.content or "")
        if not chunk_id or not content:
            continue
        document_id = _local_document_id(db, collection_id, chunk.document_id)
        row = db.scalar(
            select(RetrievalChunkCache).where(
                RetrievalChunkCache.collection_id == collection_id,
                RetrievalChunkCache.ragflow_chunk_id == chunk_id,
            )
        )
        normalized = normalize_text(content)
        if not row:
            row = RetrievalChunkCache(
                collection_id=collection_id,
                document_id=document_id,
                ragflow_dataset_id=dataset_id,
                ragflow_document_id=chunk.document_id,
                ragflow_chunk_id=chunk_id,
                content=content,
                normalized_content=normalized,
                content_hash=sha256_text(normalized),
            )
            db.add(row)
        row.document_id = document_id
        row.ragflow_dataset_id = dataset_id
        row.ragflow_document_id = chunk.document_id
        row.title = chunk.title
        row.title_path = chunk.title_path or ([chunk.title] if chunk.title else [])
        row.page_no = str((chunk.metadata or {}).get("page") or "")
        row.content = content
        row.normalized_content = normalized
        row.content_hash = sha256_text(normalized)
        row.token_count = estimate_tokens(content)
        row.score_metadata = {
            "score": chunk.score,
            "dense_score": chunk.dense_score,
            "keyword_score": chunk.keyword_score,
            "rerank_score": chunk.rerank_score,
            "source": "retrieval_response",
        }
        row.metadata_ = {"source_uri": chunk.source_uri, "retrieval_metadata": chunk.metadata or {}}
        row.available = True
        row.updated_at = datetime.utcnow()
        synced += 1
    return synced


def local_bm25_search(
    db: Session,
    *,
    collection_id: str,
    query: str,
    top_k: int = 12,
    acl_principals: list[str] | None = None,
) -> list[RetrievedChunk]:
    terms = _expanded_query_terms(db, collection_id, query)
    if not terms:
        return []

    rows = db.scalars(
        select(RetrievalChunkCache)
        .where(
            RetrievalChunkCache.collection_id == collection_id,
            RetrievalChunkCache.available.is_(True),
        )
        .limit(10000)
    ).all()
    if not rows:
        return []

    allowed_doc_ids = _allowed_document_ids(db, collection_id, acl_principals or ["public"])
    if allowed_doc_ids is not None:
        rows = [row for row in rows if row.document_id in allowed_doc_ids]
    if not rows:
        return []

    tokenized: list[tuple[RetrievalChunkCache, Counter[str]]] = []
    doc_freq: Counter[str] = Counter()
    total_len = 0
    for row in rows:
        counts = Counter(_token_sequence(row.normalized_content or row.content or ""))
        if not counts:
            continue
        title_terms = _token_sequence(" ".join([row.title or "", " ".join(row.title_path or [])]))
        for term in title_terms:
            counts[term] += 2
        tokenized.append((row, counts))
        total_len += sum(counts.values())
        doc_freq.update(set(counts))

    if not tokenized:
        return []

    query_counts = Counter(terms)
    n_docs = len(tokenized)
    avg_len = total_len / max(n_docs, 1)
    scored: list[tuple[float, RetrievalChunkCache]] = []
    for row, counts in tokenized:
        doc_len = sum(counts.values())
        score = 0.0
        for term, qtf in query_counts.items():
            tf = counts.get(term, 0)
            if not tf:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            denom = tf + 1.5 * (1 - 0.75 + 0.75 * doc_len / max(avg_len, 1))
            score += idf * (tf * 2.5 / max(denom, 1e-9)) * min(qtf, 3)
        if score > 0:
            scored.append((score, row))

    if not scored:
        return []
    max_score = max(score for score, _ in scored) or 1.0
    scored.sort(key=lambda item: item[0], reverse=True)
    chunks: list[RetrievedChunk] = []
    for score, row in scored[:top_k]:
        chunks.append(
            RetrievedChunk(
                chunk_id=row.ragflow_chunk_id,
                document_id=row.ragflow_document_id or row.document_id or "",
                title=row.title or "本地缓存片段",
                title_path=row.title_path or ([row.title] if row.title else []),
                content=row.content,
                score=score / max_score,
                dense_score=None,
                keyword_score=score / max_score,
                rerank_score=None,
                source_uri=None,
                metadata={
                    "local_cache": {
                        "id": row.id,
                        "page": row.page_no,
                        "document_id": row.document_id,
                        "ragflow_document_id": row.ragflow_document_id,
                        "retrieval_channel": "local_bm25_cache",
                    },
                    **(row.metadata_ or {}),
                },
            )
        )
    return chunks


def _expanded_query_terms(db: Session, collection_id: str, query: str) -> list[str]:
    base = _token_sequence(query)
    if not base:
        return []
    lexicon = db.scalars(
        select(DomainLexiconTerm).where(
            (DomainLexiconTerm.collection_id == collection_id) | (DomainLexiconTerm.collection_id.is_(None))
        )
    ).all()
    stopwords = {item.term for item in lexicon if item.term_type == "stopword"}
    expansions: dict[str, list[str]] = {}
    for item in lexicon:
        if item.term_type in {"synonym", "abbreviation", "alias"} and item.expansion:
            values = [part.strip() for part in re.split(r"[,，;；\s]+", item.expansion) if part.strip()]
            expansions.setdefault(item.term, []).extend(values)
    terms: list[str] = []
    for term in base:
        if term in stopwords:
            continue
        terms.append(term)
        for expanded in expansions.get(term, []):
            terms.extend(_token_sequence(expanded))
    return terms


def _allowed_document_ids(db: Session, collection_id: str, acl_principals: list[str]) -> set[str] | None:
    documents = db.scalars(select(Document).where(Document.collection_id == collection_id)).all()
    if not documents:
        return set()
    public = {"public", "*"}
    allowed: set[str] = set()
    for doc in documents:
        acl = set(doc.acl or [])
        if not acl or acl & public or acl.intersection(acl_principals):
            allowed.add(doc.id)
    return allowed


def _local_document_id(db: Session, collection_id: str, ragflow_document_id: str | None) -> str | None:
    if not ragflow_document_id:
        return None
    documents = db.scalars(select(Document).where(Document.collection_id == collection_id)).all()
    for doc in documents:
        if str((doc.metadata_ or {}).get("ragflow_document_id") or "") == str(ragflow_document_id):
            return doc.id
    return None


def _extract_chunk_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("chunks", "data", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_chunk_list(value)
            if nested:
                return nested
    return []


def _chunk_id(raw: dict) -> str:
    return str(raw.get("chunk_id") or raw.get("id") or raw.get("chunkId") or "")


def _chunk_content(raw: dict) -> str:
    return str(raw.get("content_with_weight") or raw.get("content") or raw.get("text") or raw.get("preview") or "")


def _chunk_title(raw: dict, *, fallback: str) -> str:
    return str(
        raw.get("docnm_kwd")
        or raw.get("document_name")
        or raw.get("filename")
        or raw.get("title")
        or fallback
        or "RAGFlow document"
    )


def _chunk_title_path(raw: dict, title: str) -> list[str]:
    path = raw.get("title_path") or raw.get("section_path") or []
    if isinstance(path, list):
        values = [str(item) for item in path if str(item).strip()]
    else:
        values = [str(path)] if str(path or "").strip() else []
    return values or [title]


def _page_no(raw: dict) -> str | None:
    value = raw.get("page") or raw.get("page_num") or raw.get("position") or raw.get("page_no")
    return str(value) if value not in (None, "") else None


def _score_metadata(raw: dict) -> dict:
    keys = ("similarity", "vector_similarity", "term_similarity", "rerank_score", "score")
    return {key: raw.get(key) for key in keys if raw.get(key) is not None}


def _safe_metadata(raw: dict) -> dict:
    data = dict(raw)
    for key in ("content", "content_with_weight", "text", "preview"):
        if key in data and data[key] is not None:
            data[key] = _preview(_readable_evidence_text(str(data[key])), 1000)
    return data


def _token_sequence(text: str) -> list[str]:
    sparse = tokenize_for_sparse(text or "")
    terms: list[str] = []
    for term, count in sparse.items():
        if len(term) < 2:
            continue
        terms.extend([term] * max(1, min(int(count), 20)))
    return terms


def _preview(content: str, limit: int = 520) -> str:
    text = " ".join((content or "").split())
    return text[:limit]


def _readable_evidence_text(value: str) -> str:
    text = unescape(value or "")
    if "<table" in text.lower() or "<tr" in text.lower():
        parsed = _html_table_to_text(text)
        if parsed:
            return parsed
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|table)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _html_table_to_text(value: str) -> str:
    parser = _EvidenceTableParser()
    parser.feed(value)
    if not parser.rows:
        return ""
    lines = [" | ".join(cell for cell in row if cell) for row in parser.rows]
    return "\n".join(line for line in lines if line).strip()


class _EvidenceTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th", "caption"}:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th", "caption"} and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            if self._row is None:
                self._row = []
            if text:
                self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
