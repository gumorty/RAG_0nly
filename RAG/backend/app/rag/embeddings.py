import hashlib
import math
from functools import lru_cache

from app.core.config import get_settings


class EmbeddingClient:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class LocalEmbeddingClient(EmbeddingClient):
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return vectors.tolist()


class OpenAIEmbeddingClient(EmbeddingClient):
    def __init__(self, model_name: str, base_url: str, api_key: str) -> None:
        from openai import OpenAI

        self.model_name = model_name
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(model=self.model_name, input=texts)
        return [item.embedding for item in response.data]


class HashEmbeddingClient(EmbeddingClient):
    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = text.lower().split()
        if not tokens:
            tokens = [text.lower()]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


@lru_cache
def get_embedding_client() -> EmbeddingClient:
    settings = get_settings()
    if settings.embedding_provider == "openai_compatible":
        return OpenAIEmbeddingClient(settings.embedding_model, settings.llm_base_url, settings.llm_api_key)
    if settings.embedding_provider == "hash":
        return HashEmbeddingClient()
    return LocalEmbeddingClient(settings.embedding_model)
