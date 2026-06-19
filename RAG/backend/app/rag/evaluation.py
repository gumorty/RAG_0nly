from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import EvalCase
from app.rag.schemas import RetrievalStrategy


class EvaluationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        from app.rag.retrieval import RetrievalService

        self.retrieval = RetrievalService(db)

    def run_retrieval_eval(self, collection_id: str, strategy: RetrievalStrategy) -> dict:
        cases = self.db.scalars(select(EvalCase).where(EvalCase.collection_id == collection_id)).all()
        results = []
        hit_count = 0
        reciprocal_sum = 0.0
        recall_sum = 0.0
        for case in cases:
            retrieved = self.retrieval.retrieve(collection_id, case.question, strategy, acl_principals=["public"])
            retrieved_ids = [chunk.chunk_id for chunk in retrieved]
            expected = set(case.expected_chunk_ids or [])
            hits = [chunk_id for chunk_id in retrieved_ids if chunk_id in expected]
            if hits:
                hit_count += 1
                first_rank = min(retrieved_ids.index(chunk_id) + 1 for chunk_id in hits)
                reciprocal_sum += 1.0 / first_rank
            recall = len(hits) / len(expected) if expected else 0.0
            recall_sum += recall
            results.append(
                {
                    "case_id": case.id,
                    "question": case.question,
                    "expected_chunk_ids": list(expected),
                    "retrieved_chunk_ids": retrieved_ids,
                    "hit": bool(hits),
                    "recall": recall,
                }
            )
        total = len(cases)
        return {
            "total": total,
            "hit_rate": hit_count / total if total else 0.0,
            "mrr": reciprocal_sum / total if total else 0.0,
            "context_recall": recall_sum / total if total else 0.0,
            "results": results,
        }

    def compare_strategies(self, collection_id: str, strategies: dict[str, RetrievalStrategy]) -> dict:
        results = []
        for name, strategy in strategies.items():
            metrics = self.run_retrieval_eval(collection_id, strategy)
            results.append(
                {
                    "name": name,
                    "strategy": strategy.model_dump(),
                    "total": metrics["total"],
                    "hit_rate": metrics["hit_rate"],
                    "mrr": metrics["mrr"],
                    "context_recall": metrics["context_recall"],
                }
            )
        winner = max(
            results,
            key=lambda item: (item["hit_rate"], item["mrr"], item["context_recall"]),
            default=None,
        )
        return {"collection_id": collection_id, "results": results, "winner": winner}
