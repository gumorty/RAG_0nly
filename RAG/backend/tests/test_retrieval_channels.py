from app.rag.schemas import RetrievedChunk
from app.services.retrieval_channels import build_retrieval_channels, reciprocal_rank_fusion


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
