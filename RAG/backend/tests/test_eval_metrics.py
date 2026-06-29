from app.rag.eval_metrics import (
    citation_readability_rate,
    duplicate_line_count,
    keyword_answer_score,
    retrieval_metrics,
    unsupported_claim_heuristic,
)


def test_retrieval_metrics_compute_enterprise_core_scores():
    metrics = retrieval_metrics(["c3", "c1", "c4"], ["c1", "c2"], k=3)

    assert metrics.hit is True
    assert metrics.recall_at_k == 0.5
    assert round(metrics.precision_at_k, 3) == 0.333
    assert metrics.mrr == 0.5
    assert metrics.ndcg_at_k > 0


def test_answer_quality_metrics_are_deterministic():
    answer = "Guilin amount is 1040.\nGuilin amount is 1040."
    citations = [{"preview": "The table states Guilin amount is 1040 during peak season."}]

    assert citation_readability_rate(citations) == 1.0
    assert duplicate_line_count(answer) == 1
    keyword = keyword_answer_score(answer, must_include=["Guilin", "1040"], must_not_include=["Beijing"])
    assert keyword["passed"] is True
    unsupported = unsupported_claim_heuristic(answer, citations)
    assert unsupported["unsupported_numbers"] == []
