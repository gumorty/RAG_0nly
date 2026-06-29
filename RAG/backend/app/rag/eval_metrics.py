from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievalMetrics:
    recall_at_k: float
    precision_at_k: float
    mrr: float
    ndcg_at_k: float
    hit: bool


def retrieval_metrics(
    retrieved_ids: list[str],
    expected_ids: list[str],
    k: int = 10,
) -> RetrievalMetrics:
    expected = [item for item in expected_ids if item]
    expected_set = set(expected)
    retrieved = [item for item in retrieved_ids[:k] if item]
    if not expected_set:
        return RetrievalMetrics(recall_at_k=0.0, precision_at_k=0.0, mrr=0.0, ndcg_at_k=0.0, hit=False)

    hits = [item for item in retrieved if item in expected_set]
    recall = len(set(hits)) / len(expected_set)
    precision = len(hits) / max(len(retrieved), 1)
    first_hit_rank = next((index + 1 for index, item in enumerate(retrieved) if item in expected_set), None)
    mrr = 1.0 / first_hit_rank if first_hit_rank else 0.0
    return RetrievalMetrics(
        recall_at_k=recall,
        precision_at_k=precision,
        mrr=mrr,
        ndcg_at_k=_ndcg(retrieved, expected_set, k),
        hit=bool(hits),
    )


def citation_readability_rate(citations: list[dict[str, Any]]) -> float:
    if not citations:
        return 0.0
    readable = sum(1 for citation in citations if is_readable_citation(citation))
    return readable / len(citations)


def is_readable_citation(citation: dict[str, Any]) -> bool:
    text = " ".join(str(citation.get(key) or "") for key in ("preview", "content", "text")).strip()
    if len(text) < 24:
        return False
    if "引用片段不可读" in text or "???" in text:
        return False
    garbled_chars = sum(1 for char in text if char == "\ufffd")
    return garbled_chars / max(len(text), 1) < 0.02


def duplicate_line_count(answer: str) -> int:
    seen: set[str] = set()
    duplicates = 0
    for line in (answer or "").splitlines():
        normalized = " ".join(line.split())
        if len(normalized) < 8:
            continue
        if normalized in seen:
            duplicates += 1
        seen.add(normalized)
    return duplicates


def keyword_answer_score(answer: str, must_include: list[str] | None = None, must_not_include: list[str] | None = None) -> dict:
    must_include = must_include or []
    must_not_include = must_not_include or []
    missing = [term for term in must_include if term and term not in answer]
    forbidden = [term for term in must_not_include if term and term in answer]
    total = max(len(must_include), 1)
    return {
        "keyword_coverage": (len(must_include) - len(missing)) / total,
        "missing_keywords": missing,
        "forbidden_keywords": forbidden,
        "passed": not missing and not forbidden,
    }


def unsupported_claim_heuristic(answer: str, citations: list[dict[str, Any]]) -> dict:
    """A deterministic guardrail metric before introducing LLM-as-judge."""
    citation_text = "\n".join(str(c.get("preview") or c.get("content") or "") for c in citations)
    numbers = sorted(set(re.findall(r"\d+(?:\.\d+)?%?|\d+[-~]\d+月", answer or "")))
    unsupported_numbers = [number for number in numbers if number not in citation_text]
    named_terms = sorted(set(re.findall(r"[\u4e00-\u9fff]{2,12}", answer or "")))[:80]
    unsupported_named_terms = [
        term for term in named_terms
        if term not in citation_text and term not in {"根据知识库", "证据", "文档", "回答", "如下"}
    ][:20]
    return {
        "unsupported_numbers": unsupported_numbers,
        "unsupported_named_terms_sample": unsupported_named_terms,
        "unsupported_claim_risk": bool(unsupported_numbers),
    }


def _ndcg(retrieved: list[str], expected: set[str], k: int) -> float:
    gains = [1.0 if item in expected else 0.0 for item in retrieved[:k]]
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal_len = min(len(expected), k)
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_len))
    return dcg / idcg if idcg else 0.0
