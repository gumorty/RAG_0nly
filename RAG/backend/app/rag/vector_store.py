from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.core.config import get_settings


class VectorStore:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = QdrantClient(url=self.settings.qdrant_url)
        self.collection = self.settings.qdrant_collection

    def ensure_collection(self, vector_size: int) -> None:
        existing = [collection.name for collection in self.client.get_collections().collections]
        if self.collection in existing:
            return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        )
        self.client.create_payload_index(self.collection, "collection_id", models.PayloadSchemaType.KEYWORD)
        self.client.create_payload_index(self.collection, "document_id", models.PayloadSchemaType.KEYWORD)

    def upsert_chunks(self, points: list[dict], vector_size: int) -> None:
        self.ensure_collection(vector_size)
        self.client.upsert(
            collection_name=self.collection,
            points=[
                models.PointStruct(
                    id=point["id"],
                    vector=point["vector"],
                    payload=point["payload"],
                )
                for point in points
            ],
        )

    def search(
        self,
        query_vector: list[float],
        collection_id: str,
        top_k: int,
        acl_principals: list[str] | None = None,
    ) -> list[dict]:
        must = [models.FieldCondition(key="collection_id", match=models.MatchValue(value=collection_id))]
        if acl_principals:
            must.append(
                models.FieldCondition(
                    key="acl",
                    match=models.MatchAny(any=acl_principals),
                )
            )
        result = self.client.search(
            collection_name=self.collection,
            query_vector=query_vector,
            limit=top_k,
            query_filter=models.Filter(must=must),
            with_payload=True,
        )
        return [{"chunk_id": str(item.id), "score": float(item.score), "payload": item.payload or {}} for item in result]

    def delete_document(self, document_id: str) -> None:
        collections = [collection.name for collection in self.client.get_collections().collections]
        if self.collection not in collections:
            return
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
                )
            ),
        )
