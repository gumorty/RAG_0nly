from functools import lru_cache

from app.core.config import get_settings
from app.rag.schemas import RetrievedChunk


class Reranker:
    def rerank(self, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        raise NotImplementedError


class ScoreFusionReranker(Reranker):
    def rerank(self, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        for chunk in chunks:
            dense = chunk.dense_score or 0.0
            keyword = chunk.keyword_score or 0.0
            chunk.rerank_score = 0.65 * dense + 0.35 * keyword
            chunk.score = chunk.rerank_score
        return sorted(chunks, key=lambda item: item.score, reverse=True)


class CrossEncoderReranker(Reranker):
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(model_name)

    def rerank(self, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        if not chunks:
            return []
        scores = self.model.predict([(query, chunk.content) for chunk in chunks])
        for chunk, score in zip(chunks, scores, strict=True):
            chunk.rerank_score = float(score)
            chunk.score = float(score)
        return sorted(chunks, key=lambda item: item.score, reverse=True)


@lru_cache
def get_reranker() -> Reranker:
    settings = get_settings()
    if settings.enable_reranker:
        return CrossEncoderReranker(settings.reranker_model)
    return ScoreFusionReranker()
