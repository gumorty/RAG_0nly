from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from app.rag.schemas import RetrievedChunk


@dataclass(frozen=True)
class RetrievalChannel:
    name: str
    query: str
    vector_similarity_weight: float
    similarity_threshold: float
    page_size: int
    rrf_weight: float = 1.0
    description: str = ""


def build_retrieval_channels(
    question: str,
    query_plan: list[str],
    retrieval_profile: dict[str, float | int | str],
) -> list[RetrievalChannel]:
    """Build an enterprise multi-channel retrieval plan around RAGFlow retrieval."""
    base_page_size = int(retrieval_profile["page_size"])
    base_threshold = float(retrieval_profile["similarity_threshold"])
    base_vector_weight = float(retrieval_profile["vector_similarity_weight"])
    terms = _important_terms(question)
    synonyms = _expand_synonyms(question)
    intent = detect_query_intent(question)

    channels: list[RetrievalChannel] = [
        RetrievalChannel(
            name="semantic",
            query=query_plan[0] if query_plan else question,
            vector_similarity_weight=max(base_vector_weight, 0.65),
            similarity_threshold=max(0.01, base_threshold * 0.75),
            page_size=max(base_page_size, 12),
            rrf_weight=1.0,
            description="语义召回",
        ),
        RetrievalChannel(
            name="keyword_bm25",
            query=" ".join(terms) or question,
            vector_similarity_weight=0.05,
            similarity_threshold=0.0,
            page_size=max(base_page_size, 12),
            rrf_weight=0.95,
            description="关键词/BM25 召回",
        ),
    ]

    if len(query_plan) > 1:
        for index, query in enumerate(query_plan[1:4], start=1):
            channels.append(
                RetrievalChannel(
                    name=f"rewrite_{index}",
                    query=query,
                    vector_similarity_weight=base_vector_weight,
                    similarity_threshold=max(0.01, base_threshold * 0.85),
                    page_size=max(base_page_size // 2, 8),
                    rrf_weight=0.8,
                    description="查询改写召回",
                )
            )

    if synonyms:
        channels.append(
            RetrievalChannel(
                name="synonym_expansion",
                query=f"{question} {' '.join(synonyms)}",
                vector_similarity_weight=0.35,
                similarity_threshold=max(0.01, base_threshold * 0.7),
                page_size=max(base_page_size, 10),
                rrf_weight=0.75,
                description="同义词/领域词扩展召回",
            )
        )

    if intent in {"table", "numeric"}:
        channels.append(
            RetrievalChannel(
                name="table_row",
                query=f"{question} 表格行级检索索引 字段 数值 标准 金额 期间",
                vector_similarity_weight=0.15,
                similarity_threshold=0.0,
                page_size=max(base_page_size, 14),
                rrf_weight=1.15,
                description="表格行召回",
            )
        )

    if intent in {"section", "definition", "procedure"} or _looks_like_section_query(question):
        channels.append(
            RetrievalChannel(
                name="section",
                query=f"{question} 章节 标题 小节 段落 要点",
                vector_similarity_weight=0.25,
                similarity_threshold=max(0.0, base_threshold * 0.5),
                page_size=max(base_page_size, 10),
                rrf_weight=0.85,
                description="章节召回",
            )
        )
        channels.append(
            RetrievalChannel(
                name="title",
                query=f"{question} 文件名 标题 目录 摘要",
                vector_similarity_weight=0.1,
                similarity_threshold=0.0,
                page_size=max(base_page_size // 2, 8),
                rrf_weight=0.7,
                description="标题召回",
            )
        )

    return _dedupe_channels(channels)[:8]


def detect_query_intent(question: str) -> str:
    text = question or ""
    if any(term in text for term in ("表", "金额", "数量", "多少", "比例", "标准", "上浮", "浮动", "期间", "几月")):
        return "table" if any(term in text for term in ("表", "标准", "金额", "上浮", "浮动")) else "numeric"
    if any(term in text for term in ("是什么", "定义", "概念", "含义", "解释")):
        return "definition"
    if any(term in text for term in ("步骤", "流程", "如何", "怎么", "方案", "办法")):
        return "procedure"
    if any(term in text for term in ("章节", "第几章", "目录", "标题", "小节")):
        return "section"
    if any(term in text for term in ("代码", "函数", "类", "接口", "配置", "报错")):
        return "code"
    return "semantic"


def reciprocal_rank_fusion(
    runs: list[tuple[RetrievalChannel, list[RetrievedChunk]]],
    limit: int,
    question: str,
    k: int = 60,
) -> list[RetrievedChunk]:
    fused: dict[str, dict[str, Any]] = {}
    for channel, chunks in runs:
        for rank, chunk in enumerate(chunks, start=1):
            key = _chunk_key(chunk)
            item = fused.setdefault(
                key,
                {
                    "chunk": chunk.model_copy(deep=True),
                    "rrf": 0.0,
                    "best_score": float(chunk.score or 0.0),
                    "channels": [],
                    "queries": [],
                },
            )
            item["rrf"] += channel.rrf_weight / (k + rank)
            item["best_score"] = max(float(item["best_score"]), float(chunk.score or 0.0))
            item["channels"].append(channel.name)
            item["queries"].append(channel.query)

    ranked: list[RetrievedChunk] = []
    for item in fused.values():
        chunk: RetrievedChunk = item["chunk"]
        deep_score = distilled_rank_score(question, chunk, item["channels"])
        combined = float(item["rrf"]) + deep_score
        chunk.score = max(float(chunk.score or 0.0), combined)
        chunk.metadata = {
            **(chunk.metadata or {}),
            "retrieval_channels": sorted(set(item["channels"])),
            "query_variants": sorted(set(item["queries"])),
            "rrf_score": round(float(item["rrf"]), 6),
            "distilled_rank_score": round(deep_score, 6),
            "original_best_score": round(float(item["best_score"]), 6),
        }
        ranked.append(chunk)
    return sorted(ranked, key=lambda chunk: chunk.score, reverse=True)[:limit]


def distilled_rank_score(question: str, chunk: RetrievedChunk, channels: list[str]) -> float:
    """Small deterministic ranker used after RRF and before answer generation."""
    content = f"{chunk.title}\n{' / '.join(chunk.title_path or [])}\n{chunk.content}"
    terms = _important_terms(question)
    if not content or not terms:
        return 0.0
    term_hits = sum(1 for term in terms if term in content)
    coverage = term_hits / max(len(terms), 1)
    proximity = _term_proximity_bonus(content, terms)
    table_bonus = 0.05 if "table_row" in channels and "表格" in content else 0.0
    title_bonus = 0.04 if "title" in channels and any(term in chunk.title for term in terms) else 0.0
    return min(0.28, coverage * 0.16 + proximity + table_bonus + title_bonus)


def _term_proximity_bonus(content: str, terms: list[str]) -> float:
    positions = []
    for term in terms[:8]:
        index = content.find(term)
        if index >= 0:
            positions.append(index)
    if len(positions) < 2:
        return 0.0
    spread = max(positions) - min(positions)
    if spread <= 220:
        return 0.08
    if spread <= 600:
        return 0.04
    return 0.0


def _important_terms(question: str) -> list[str]:
    text = re.sub(r"[\s,，。！？；;:：、（）()【】\[\]\"']", " ", question or "")
    tokens = []
    for token in text.split():
        if len(token) >= 2 and token not in _STOPWORDS:
            tokens.append(token)
    for match in re.findall(r"[\u4e00-\u9fff]{2,12}", question or ""):
        if match not in _STOPWORDS:
            tokens.append(match)
    return _dedupe(tokens)[:16]


def _expand_synonyms(question: str) -> list[str]:
    terms: list[str] = []
    for key, values in _SYNONYMS.items():
        if key in question:
            terms.extend(values)
    return _dedupe(terms)[:20]


def _looks_like_section_query(question: str) -> bool:
    return bool(re.search(r"第[一二三四五六七八九十\d]+[章节条]|[一二三四五六七八九十\d]+、", question or ""))


def _dedupe_channels(channels: list[RetrievalChannel]) -> list[RetrievalChannel]:
    seen: set[tuple[str, str]] = set()
    result: list[RetrievalChannel] = []
    for channel in channels:
        key = (channel.name, channel.query)
        if key in seen:
            continue
        seen.add(key)
        result.append(channel)
    return result


def _chunk_key(chunk: RetrievedChunk) -> str:
    return chunk.chunk_id or f"{chunk.document_id}:{hash(chunk.content[:500])}"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


_STOPWORDS = {
    "什么", "哪些", "怎么", "如何", "是否", "有没有", "这个", "那个", "以及", "根据", "当前", "里面",
    "文档", "资料", "知识库", "请问", "一下", "进行", "关于",
}

_SYNONYMS = {
    "旺季": ["旅游旺季", "旺季期间", "上浮", "浮动", "上浮标准"],
    "浮动": ["上浮", "调整", "浮动标准", "上浮比例"],
    "住宿费": ["差旅住宿费", "住宿费标准", "住宿标准"],
    "解析": ["抽取", "识别", "结构化", "OCR", "表格识别"],
    "切片": ["分块", "chunk", "片段", "文本块"],
    "召回": ["检索", "搜索", "retrieval", "命中"],
    "模型": ["大模型", "LLM", "chat model"],
    "论文": ["paper", "文献", "研究", "参考文献"],
}
