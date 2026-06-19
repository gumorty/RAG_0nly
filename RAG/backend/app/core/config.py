from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_secret: str = "change-me"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
