"""Evaluation service — supports RAGFlow-aware retrieval evaluation and strategy comparison.

Optimization analysis — Evaluation:

1. Problem: 之前 eval_cases 表始终为 0，从未创建过评估用例
   Solution: 新增 ``generate_eval_cases_from_traces()``，从历史 retrieval_traces
            自动生成 EvalCase 基线（低证据回答 + 用户修正问题 → 持续回归集）

2. Problem: 本地评估只对比本地 RetrievalStrategy 组合，无 RAGFlow 基线
   Solution: 新增 ``run_ragflow_retrieval_eval()`` + 策略对比时自动添加
           ``"ragflow_default"`` 策略作为基线

执行链路::

    ① POST /eval/{collection_id}/compare
       ├── 对于每个策略: run_*_eval()
       ├── 自动追加 "ragflow_default" 基线策略
       └── 返回 hit_rate / MRR / recall 对比表 + 最优策略

    ② POST /answers/{id}/to-eval-case (已有)
       └── 手动将低证据回答转为 EvalCase

    ③ generate_eval_cases_from_traces() (新增定时/手动触发)
       └── 从 retrieval_traces 批量生成 EvalCase 基线
"""

import logging
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Answer, Collection, EvalCase, RetrievalTrace
from app.rag.schemas import RetrievalStrategy
from app.ragflow.client import RagFlowClient

logger = logging.getLogger(__name__)


class EvaluationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        from app.rag.retrieval import RetrievalService

        self.retrieval = RetrievalService(db)

    def run_retrieval_eval(self, collection_id: str, strategy: RetrievalStrategy) -> dict:
        """Evaluate a local retrieval strategy against the collection's EvalCases.

        Returns:
            ``{total, hit_rate, mrr, context_recall, results}``
        """
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

    def run_ragflow_retrieval_eval(self, collection_id: str) -> dict:
        """Evaluate RAGFlow's built-in retrieval pipeline against EvalCases.

        Uses ``RagFlowClient.retrieve()`` to test RAGFlow's native retrieval
        (dense + sparse + optional reranker), providing a baseline for
        comparison with local strategies.

        Returns:
            ``{engine: "ragflow", total, hit_rate, mrr, context_recall, results}``
        """
        collection = self.db.scalar(select(Collection).where(Collection.id == collection_id))
        dataset_id = (collection.metadata_ or {}).get("ragflow_dataset_id") if collection else None
        if not dataset_id:
            return {"engine": "ragflow", "error": "No RAGFlow dataset linked to this collection"}

        client = RagFlowClient()
        cases = self.db.scalars(select(EvalCase).where(EvalCase.collection_id == collection_id)).all()

        results = []
        hit_count = 0
        reciprocal_sum = 0.0
        recall_sum = 0.0

        for case in cases:
            try:
                chunks, _ = client.retrieve(str(dataset_id), case.question, page_size=10)
            except Exception as exc:
                logger.warning("RAGFlow eval retrieval failed for case %s: %s", case.id, exc)
                continue

            retrieved_ids = [ch.chunk_id for ch in chunks]
            expected = set(case.expected_chunk_ids or [])
            hits = [cid for cid in retrieved_ids if cid in expected]

            if hits:
                hit_count += 1
                first_rank = min(retrieved_ids.index(cid) + 1 for cid in hits)
                reciprocal_sum += 1.0 / first_rank
            recall = len(hits) / len(expected) if expected else 0.0
            recall_sum += recall

            results.append({
                "case_id": case.id,
                "question": case.question,
                "expected_chunk_ids": list(expected),
                "retrieved_chunk_ids": retrieved_ids,
                "hit": bool(hits),
                "recall": recall,
            })

        total = len(cases)
        return {
            "engine": "ragflow",
            "total": total,
            "hit_rate": hit_count / total if total else 0.0,
            "mrr": reciprocal_sum / total if total else 0.0,
            "context_recall": recall_sum / total if total else 0.0,
            "results": results,
        }

    def compare_strategies(self, collection_id: str, strategies: dict[str, RetrievalStrategy]) -> dict:
        """Compare multiple retrieval strategies + auto-add RAGFlow baseline.

        Args:
            collection_id: Target collection.
            strategies: ``{name: RetrievalStrategy}`` dict.

        Returns:
            ``{collection_id, results: [...], winner: ...}``
        """
        results = []
        for name, strategy in strategies.items():
            metrics = self.run_retrieval_eval(collection_id, strategy)
            results.append({
                "name": name,
                "strategy": strategy.model_dump(),
                "total": metrics["total"],
                "hit_rate": metrics["hit_rate"],
                "mrr": metrics["mrr"],
                "context_recall": metrics["context_recall"],
            })

        # Auto-add RAGFlow baseline if there are eval cases
        case_count = self.db.scalar(
            select(EvalCase.id).where(EvalCase.collection_id == collection_id).limit(1)
        )
        if case_count is not None:
            ragflow_metrics = self.run_ragflow_retrieval_eval(collection_id)
            if "error" not in ragflow_metrics:
                results.append({
                    "name": "ragflow_baseline",
                    "strategy": {"engine": "ragflow_default"},
                    "total": ragflow_metrics["total"],
                    "hit_rate": ragflow_metrics["hit_rate"],
                    "mrr": ragflow_metrics["mrr"],
                    "context_recall": ragflow_metrics["context_recall"],
                })

        winner = max(
            results,
            key=lambda item: (item.get("hit_rate", 0), item.get("mrr", 0), item.get("context_recall", 0)),
            default=None,
        )
        return {"collection_id": collection_id, "results": results, "winner": winner}


def generate_eval_cases_from_traces(db: Session, collection_id: str | None = None) -> int:
    """Auto-generate EvalCases from historical retrieval traces.

    Heuristic — creates EvalCases for:
      1. Answers with ``evidence_score < 0.15`` (low evidence → knowledge gap)
      2. Answers with negative user feedback (feedback in {"negative","incorrect","missing_source"})
      3. Answers where the assistant explicitly stated insufficient evidence

    This builds a baseline evaluation set without manual effort.
    """
    stmt = select(Answer)
    if collection_id:
        stmt = stmt.where(Answer.collection_id == collection_id)
    answers = db.scalars(stmt.order_by(Answer.created_at.desc()).limit(200)).all()

    created = 0
    for answer in answers:
        # Skip if an EvalCase already exists for this question + collection
        existing = db.scalar(
            select(EvalCase).where(
                EvalCase.collection_id == answer.collection_id,
                EvalCase.question == answer.question,
            )
        )
        if existing:
            continue

        # Only auto-create for low-evidence / negative feedback cases
        should_create = (
            answer.evidence_score < 0.15
            or answer.feedback in ("negative", "incorrect", "missing_source")
        )
        if not should_create:
            # Check if assistant said something like "insufficient evidence"
            insufficient_phrases = [
                "没有足够", "没有找到", "无法回答", "没有任何信息",
                "不足", "缺少", "no sufficient", "cannot answer",
            ]
            if answer.answer:
                text_lower = answer.answer.lower()
                if any(p in text_lower for p in insufficient_phrases):
                    should_create = True

        if not should_create:
            continue

        case = EvalCase(
            collection_id=answer.collection_id,
            question=answer.question,
            expected_answer=None,
            expected_chunk_ids=[c.get("chunk_id") for c in (answer.citations or []) if c.get("chunk_id")],
            metadata_={
                "source": "auto_from_logs",
                "answer_id": answer.id,
                "evidence_score": answer.evidence_score,
                "feedback": answer.feedback,
            },
        )
        db.add(case)
        created += 1

    if created:
        db.commit()
        logger.info("Auto-generated %d EvalCases from retrieval traces", created)

    return created
