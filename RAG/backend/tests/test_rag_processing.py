from app.rag.analysis import build_document_analysis, extract_lab_report_signals
from app.rag.chunking import HierarchicalChunker
from app.rag.normalize import normalize_text, tokenize_for_sparse
from app.rag.schemas import ParsedDocument


def test_normalize_text_collapses_noise() -> None:
    text = " 第一行\r\n\r\n\r\n 第二\t\t行  "
    assert normalize_text(text) == "第一行\n\n第二 行"


def test_hierarchical_chunker_preserves_title_path() -> None:
    parsed = ParsedDocument(
        title="weekly",
        text="",
        sections=[
            {"title_path": ["项目A", "进展"], "text": "完成了数据清洗。\n\n下一步训练模型。"},
            {"title_path": ["项目A", "风险"], "text": "GPU 资源不足。"},
        ],
    )
    chunks = HierarchicalChunker(max_tokens=20, overlap_tokens=4).chunk(parsed)
    assert chunks
    assert chunks[0].title_path == ["项目A", "进展"]
    assert any(chunk.title_path == ["项目A", "风险"] for chunk in chunks)


def test_sparse_terms_keep_chinese_and_project_terms() -> None:
    terms = tokenize_for_sparse("无人机项目 Qwen3 模型 本周完成无人机测试")
    assert terms["无人机项目"] == 1
    assert terms["qwen3"] == 1
    assert terms["无人机"] >= 2


def test_lab_report_signal_extraction() -> None:
    text = """
    汇报人：张三
    本周完成无人机数据清洗和 Qwen RAG 测试。
    风险：GPU 资源不足，部分 PDF 解析失败。
    下周计划：补充评估集并优化 rerank 策略。
    结论：采用混合检索作为默认方案。
    """
    signals = extract_lab_report_signals(text)
    assert "张三" in signals["owners"]
    assert any("数据清洗" in item for item in signals["progress"])
    assert any("GPU" in item for item in signals["risks"])
    assert any("评估集" in item for item in signals["next_steps"])
    assert any("混合检索" in item for item in signals["decisions"])


def test_document_analysis_reports_chunk_metrics() -> None:
    parsed = ParsedDocument(
        title="weekly",
        text="本周完成检索测试。\n\n下周计划补充评估集。",
        sections=[{"title_path": ["周报"], "text": "本周完成检索测试。\n\n下周计划补充评估集。"}],
    )
    chunks = HierarchicalChunker(max_tokens=40, overlap_tokens=0).chunk(parsed)
    analysis = build_document_analysis(parsed, chunks)
    assert analysis["chunk_count"] == len(chunks)
    assert analysis["estimated_tokens"] > 0
    assert "lab_signals" in analysis
