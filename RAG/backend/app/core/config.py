from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_secret: str = "change-me"
    public_registration_enabled: bool = True
    cors_allowed_origins: str = "http://localhost:14070,http://localhost:4070,http://localhost:3010,http://localhost:3000"
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_name: str = "RAG Admin"
    bootstrap_admin_api_key: str = "change-this-admin-api-key"
    bootstrap_admin_password: str = "Admin@123456"
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    database_url: str
    redis_url: str = "redis://redis:6379/0"

    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "rag_chunks"

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "rag_minio"
    minio_secret_key: str = "rag_minio_password"
    minio_bucket: str = "rag-documents"
    minio_secure: bool = False

    llm_provider: str = "openai_compatible"
    llm_base_url: str = "http://host.docker.internal:8000/v1"
    llm_api_key: str = "change-me"
    llm_model: str = "qwen3.6-35b-a3b-fp8"

    embedding_provider: str = "local"
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    enable_reranker: bool = False

    chunk_max_tokens: int = Field(default=600, ge=128, le=4000)
    chunk_overlap_tokens: int = Field(default=100, ge=0, le=1000)
    retrieval_dense_top_k: int = Field(default=30, ge=1, le=200)
    retrieval_keyword_top_k: int = Field(default=30, ge=1, le=200)
    retrieval_final_top_k: int = Field(default=8, ge=1, le=50)
    min_answer_evidence_score: float = Field(default=0.22, ge=0.0, le=1.0)
    ingestion_mode: str = Field(default="sync", pattern="^(sync|async)$")
    max_upload_size_mb: int = Field(default=100, ge=1, le=2048)
    max_zip_upload_size_mb: int = Field(default=500, ge=1, le=4096)
    allow_private_url_ingest: bool = False

    ragflow_enabled: bool = True
    ragflow_base_url: str = "http://ragflow-gpu:9380/api/v1"
    ragflow_api_key: str = "ragflow-llmstart-local-20260619"
    ragflow_chat_model: str = "qwen3.7-plus@default@OpenAI-API-Compatible"
    ragflow_embedding_model: str = "text-embedding-v4@default@OpenAI-API-Compatible"
    ragflow_parse_timeout_seconds: int = Field(default=900, ge=30, le=7200)
    ragflow_sync_interval_seconds: int = Field(default=120, ge=30, le=3600)
    ragflow_enable_chat_completions: bool = True
    ragflow_default_chunk_method: str = "naive"
    ragflow_reranker_model: str = ""
    ragflow_enable_agent: bool = False
    ragflow_agent_template: str = "qa_agent"
    ragflow_kg_enabled_default: bool = False

    mineru_enabled: bool = False
    mineru_base_url: str = "https://mineru.net"
    mineru_api_key: str = ""
    mineru_model_version: str = "vlm"
    mineru_language: str = "ch"
    mineru_timeout_seconds: int = Field(default=600, ge=30, le=3600)
    mineru_poll_interval_seconds: int = Field(default=5, ge=1, le=60)
    mineru_max_file_size_mb: int = Field(default=200, ge=1, le=200)
    mineru_pdf_ocr_mode: str = Field(default="auto", pattern="^(auto|always|never)$")
    table_row_index_enabled: bool = True
    table_row_index_max_rows: int = Field(default=5000, ge=100, le=50000)

    sciverse_enabled: bool = False
    sciverse_base_url: str = "https://api.sciverse.space"
    sciverse_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
