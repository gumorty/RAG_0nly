from app.rag.embeddings import HashEmbeddingClient
from app.core.config import get_settings
from app.rag.llm import LLMClient
from app.rag.schemas import RetrievedChunk


def test_hash_embedding_is_deterministic_and_normalized() -> None:
    client = HashEmbeddingClient(dimensions=32)
    first = client.embed_query("rag smoke test")
    second = client.embed_query("rag smoke test")
    assert first == second
    assert len(first) == 32
    assert abs(sum(value * value for value in first) - 1.0) < 1e-6


def test_mock_llm_answer_uses_retrieved_chunks(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    client = LLMClient()
    answer = client.answer(
        "What happened?",
        [
            RetrievedChunk(
                chunk_id="chunk-1",
                document_id="doc-1",
                title="Weekly",
                content="Progress: indexing completed.",
                score=1.0,
            )
        ],
        min_score=0.0,
    )
    assert "Mock grounded answer" in answer
    assert "indexing completed" in answer
    get_settings.cache_clear()
