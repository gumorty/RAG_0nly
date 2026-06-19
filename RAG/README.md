# Enterprise RAG Knowledge Base

面向实验室和企业私有资料的 RAG 知识库管理系统。当前版本采用 Python/FastAPI 后端、Next.js 前端、PostgreSQL、Redis、Qdrant 和 MinIO，通过 Docker Compose 一键启动。

## Architecture

- Backend: Python + FastAPI + SQLAlchemy + Celery
- Frontend: Next.js chat-first knowledge-base workspace
- Auth: JWT access token + refresh token
- Database: PostgreSQL
- Queue/cache: Redis
- Vector store: Qdrant
- Object storage: MinIO
- Retrieval: dense/hash embedding + sparse keyword hybrid scoring
- Deployment: Docker Compose

## Quick Start

```powershell
cd D:\Researching\LLMStart\RAG
Copy-Item .env.example .env
docker compose up --build -d
```

Services:

- Frontend: http://localhost:3110
- API: http://localhost:8010/api/health
- MinIO API: http://localhost:9100
- MinIO Console: http://localhost:9101

Default login:

- Email: `admin@example.com`
- Password: `Admin@123456`

Change `ADMIN_API_KEY`, `APP_SECRET`, and the bootstrap admin password before deploying outside a trusted local environment.

## Model Routing

The app starts with a mock model so the RAG workflow can run without a third-party key. To use Alibaba Cloud Model Studio / Bailian, open the left-side model routing panel and add:

- Provider: `openai_compatible`
- Model: `qwen-plus`
- Base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- API Key: your Bailian/DashScope API key

After saving and activating the model, `/api/chat` uses the active row in `model_configs`.

## RAG Processing Chain

1. Ingest local files, URL pages, or ZIP batches.
2. Deduplicate documents by SHA-256 checksum inside each collection.
3. Parse supported text-oriented files and normalize extracted content.
4. Clean encoding, whitespace, line breaks, and noisy text.
5. Split documents with token-aware hierarchical chunking and overlap windows.
6. Store chunk text, token count, title path, sparse terms, and document metadata.
7. Build document quality reports and source-aware summaries.
8. Extract enterprise signals: progress, risks, next steps, and decisions from actual document content.
9. Retrieve with hybrid scoring and optional reranking/context expansion.
10. Answer only from retrieved evidence, return citations, and record low-evidence knowledge gaps.

See [docs/RAG_SYSTEM_DESIGN.md](docs/RAG_SYSTEM_DESIGN.md).
