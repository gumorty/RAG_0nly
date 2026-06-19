# RAG Knowledge Base System Design

## Goal

The system starts with a lab weekly-report knowledge base and is designed to expand into an enterprise knowledge governance platform. The core value is strict document processing, retrieval quality control, citation traceability, and a continuous evaluation loop.

## Current Python Implementation

Backend runtime:

- Python + FastAPI
- SQLAlchemy/PostgreSQL persistence
- Celery worker for asynchronous ingestion
- Redis broker/result backend
- Qdrant vector store
- MinIO object storage
- JWT access token + refresh token authentication
- Admin API key guard through `X-API-Key`

Frontend runtime:

- Next.js chat-first management console
- Collection navigation
- Document ingestion workbench
- Dynamic model routing panel
- Document quality board
- Grounded Q&A
- Knowledge gap operations panel
- Source-aware material summary

## Knowledge Processing Chain

### 1. Ingestion

Supported inputs:

- Single local file upload
- URL page import
- ZIP batch import

Strict controls:

- SHA-256 checksum deduplication per collection
- ZIP system-path filtering for `__MACOSX`, `.DS_Store`, hidden files, and unsupported extensions
- File type mapping by suffix and content type
- Metadata capture: author, project, meeting date, tags, source URI

### 2. Parsing

The parser extracts text from supported office/text/web formats. If no text can be extracted, ingestion fails instead of indexing empty content.

### 3. Normalization

Before chunking, all text is normalized:

- Unicode normalization
- NBSP cleanup
- CRLF and CR newline normalization
- repeated blank-line compression
- whitespace cleanup

This prevents inconsistent retrieval caused by mixed document formats.

### 4. Chunking

The service uses token-aware hierarchical chunking:

- split by paragraphs first
- split long paragraphs by sentence punctuation
- keep configurable overlap windows
- store token estimate, title path, normalized text, and sparse terms for every chunk

Default parameters:

- `CHUNK_MAX_TOKENS=600`
- `CHUNK_OVERLAP_TOKENS=100`

### 5. Quality Analysis

Each document produces an analysis report:

- character count
- estimated token count
- chunk count
- min, max, and average chunk tokens
- top sparse terms
- quality warnings
- report signals

Report signals include:

- owners
- progress
- risks
- next steps
- decisions

### 6. Retrieval

Current local deployable retrieval uses:

- Hash embedding vector for semantic-like matching without external model dependency
- Sparse keyword overlap for exact project names, people, dates, and technical terms
- weighted hybrid score
- top-k evidence selection

This is intentionally deployable on a normal server without GPU or model service. For enterprise production, the retrieval layer should be upgraded to real embeddings and reranking while preserving the same API and metadata model.

### 7. Grounded Answering

The backend answers only from selected chunks:

- low evidence returns a refusal message
- every answer stores citations
- every answer stores evidence score
- knowledge gaps are generated from low evidence, missing citations, or negative feedback
- the active model route is read from PostgreSQL at chat time

## Enterprise Upgrade Path

Next technical milestones:

1. Replace hash embedding with bge-m3, OpenAI-compatible embeddings, or an internal embedding service.
2. Add stronger dense vector scoring in Qdrant and optional PostgreSQL pgvector support.
3. Add sparse vector or OpenSearch/BM25 for stronger keyword retrieval.
4. Add cross-encoder reranking.
5. Add eval-case tables and strategy comparison metrics: Hit Rate, MRR, Context Recall, citation accuracy.
6. Add enterprise SSO/OAuth.
7. Add tenant isolation and document ACL filtering.
8. Add document versioning and incremental reindexing.
9. Add sensitive data detection and redaction.
10. Add connectors for Notion, Feishu, Git, database records, and shared drives.
