from app.rag.schemas import RetrievedChunk
from app.services.retrieval_channels import build_retrieval_channels, detect_query_intent, reciprocal_rank_fusion


def _chunk(chunk_id: str, content: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        title="Policy table",
        content=content,
        score=score,
    )


def test_build_retrieval_channels_adds_table_and_keyword_channels():
    channels = build_retrieval_channels(
        "广西住宿费旺季上浮标准是多少？",
        ["广西住宿费旺季上浮标准是多少？"],
        {"page_size": 8, "similarity_threshold": 0.05, "vector_similarity_weight": 0.3},
    )

    names = {channel.name for channel in channels}
    assert "semantic" in names
    assert "keyword_bm25" in names
    assert "table_row" in names
    assert "synonym_expansion" in names


def test_rrf_fuses_channels_and_keeps_channel_metadata():
    channels = build_retrieval_channels(
        "Guangxi amount",
        ["Guangxi amount"],
        {"page_size": 4, "similarity_threshold": 0.05, "vector_similarity_weight": 0.3},
    )
    semantic = channels[0]
    keyword = channels[1]

    ranked = reciprocal_rank_fusion(
        [
            (semantic, [_chunk("a", "Guangxi Guilin amount 1040", 0.5), _chunk("b", "Other", 0.4)]),
            (keyword, [_chunk("a", "Guangxi Guilin amount 1040", 0.3), _chunk("c", "Amount", 0.2)]),
        ],
        limit=3,
        question="Guangxi amount",
    )

    assert ranked[0].chunk_id == "a"
    assert "semantic" in ranked[0].metadata["retrieval_channels"]
    assert "keyword_bm25" in ranked[0].metadata["retrieval_channels"]
    assert ranked[0].metadata["rrf_score"] > 0


def test_build_retrieval_channels_adds_figure_channel():
    channels = build_retrieval_channels(
        "图6展示了什么？在哪一页？",
        ["图6展示了什么？在哪一页？"],
        {"page_size": 8, "similarity_threshold": 0.05, "vector_similarity_weight": 0.3},
    )

    names = {channel.name for channel in channels}
    assert detect_query_intent("图6展示了什么？") == "figure"
    assert "figure_index" in names


def test_rrf_promotes_figure_index_over_catalog_for_figure_question():
    channels = build_retrieval_channels(
        "图6展示了什么？",
        ["图6展示了什么？"],
        {"page_size": 4, "similarity_threshold": 0.05, "vector_similarity_weight": 0.3},
    )
    figure = next(channel for channel in channels if channel.name == "figure_index")
    semantic = channels[0]

    ranked = reciprocal_rank_fusion(
        [
            (semantic, [_chunk("toc", "图目录 图 6 AI 应用产业链分布 ...... 35", 0.8)]),
            (figure, [_chunk("fig", "# 图表检索索引\n## 图6 AI 应用产业链分布\n- 页码线索：第 35 页\n- 邻近文本：图6说明产业链分布", 0.5)]),
        ],
        limit=2,
        question="图6展示了什么？",
    )

    assert ranked[0].chunk_id == "fig"
