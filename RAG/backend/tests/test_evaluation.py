from app.rag.evaluation import EvaluationService
from app.rag.schemas import RetrievalStrategy


def test_compare_strategy_winner_prefers_hit_rate_then_mrr() -> None:
    service = EvaluationService.__new__(EvaluationService)
    calls = {
        30: {"total": 3, "hit_rate": 0.5, "mrr": 0.4, "context_recall": 0.5},
        60: {"total": 3, "hit_rate": 0.8, "mrr": 0.3, "context_recall": 0.7},
    }

    def fake_eval(collection_id, strategy):
        return calls[strategy.dense_top_k]

    service.run_retrieval_eval = fake_eval
    result = service.compare_strategies(
        "collection-id",
        {
            "baseline": RetrievalStrategy(dense_top_k=30, keyword_top_k=30, final_top_k=8),
            "high_recall": RetrievalStrategy(dense_top_k=60, keyword_top_k=60, final_top_k=10),
        },
    )

    assert result["winner"]["name"] == "high_recall"
