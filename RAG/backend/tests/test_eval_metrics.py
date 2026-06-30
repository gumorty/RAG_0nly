from app.rag.eval_metrics import (
    citation_readability_rate,
    duplicate_line_count,
    evidence_pollution_rate,
    figure_hit_rate,
    keyword_answer_score,
    retrieval_metrics,
    toc_over_rank_rate,
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


def test_enterprise_rag_quality_metrics_detect_pollution_and_figure_hits():
    citations = [
        {"preview": "# 图表检索索引 ## 图6 AI 应用产业链分布 - 页码线索：第 35 页"},
        {"preview": "图目录 图 7 其他内容 ...... 40"},
    ]

    assert evidence_pollution_rate(citations, question="图6展示了什么？") == 0.0
    assert figure_hit_rate("图6展示了什么？", citations) == 1.0
    assert toc_over_rank_rate(citations) == 0.5

    polluted = [{"preview": "住宿费 旺季 上浮 相邻地区"}]
    assert evidence_pollution_rate(polluted, question="图6展示了什么？") == 1.0
