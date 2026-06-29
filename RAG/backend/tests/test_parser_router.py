from types import SimpleNamespace

from app.services.document_quality import score_ingest_candidate, score_parsed_text
from app.services import parser_router


def _settings(**overrides):
    defaults = {
        "mineru_enabled": False,
        "mineru_api_key": "",
        "mineru_max_file_size_mb": 200,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_parser_router_keeps_ragflow_when_mineru_disabled(monkeypatch):
    monkeypatch.setattr(parser_router, "get_settings", lambda: _settings(mineru_enabled=False))

    decision = parser_router._select_parser_engine("report.pdf", b"%PDF-1.7", "application/pdf")

    assert decision["engine"] == "ragflow_deepdoc"
    assert decision["reason"] == "mineru_disabled"


def test_parser_router_selects_mineru_for_complex_pdf_when_configured(monkeypatch):
    monkeypatch.setattr(
        parser_router,
        "get_settings",
        lambda: _settings(mineru_enabled=True, mineru_api_key="secret"),
    )

    decision = parser_router._select_parser_engine("report.pdf", b"%PDF-1.7", "application/pdf")

    assert decision["engine"] == "mineru_precise"
    assert decision["reason"] == "complex_document_type"


def test_raw_quality_marks_images_as_ocr_candidates():
    score = score_ingest_candidate("scan.png", b"not-empty", "image/png", "ragflow_deepdoc")

    assert score["score"] < 1.0
    assert "image_or_scan_requires_ocr" in score["warnings"]


def test_parsed_quality_detects_garbled_text():
    score = score_parsed_text("有效文本" + "\ufffd" * 20, "mineru_precise")

    assert score["score"] < 1.0
    assert "garbled_characters" in score["warnings"]


def test_prepare_for_ragflow_appends_table_index_after_mineru(monkeypatch):
    class FakeMinerUClient:
        def parse_file(self, filename, data, data_id=None):
            return SimpleNamespace(
                markdown="""
| 地区 | 城市 | 普通_部级 | 普通_司局级 | 旺季期间 | 旺季_司局级 |
|---|---|---:|---:|---|---:|
| 河北 | 秦皇岛市 | 800 | 450 | 7-8月 | 680 |
""",
                metadata={"mineru_batch_id": "test-batch"},
            )

    monkeypatch.setattr(
        parser_router,
        "get_settings",
        lambda: _settings(
            mineru_enabled=True,
            mineru_api_key="secret",
            table_row_index_enabled=True,
            table_row_index_max_rows=5000,
        ),
    )
    monkeypatch.setattr(parser_router, "MinerUClient", FakeMinerUClient)

    name, data, content_type, metadata = parser_router.prepare_for_ragflow(
        "doc-1",
        "policy.pdf",
        b"%PDF-1.7 fake",
        "application/pdf",
    )

    text = data.decode("utf-8")
    assert name == "policy.pdf.mineru.md"
    assert content_type == "text/markdown"
    assert "表格行级检索索引" in text
    assert "城市=秦皇岛市" in text
    assert metadata["table_index"]["table_row_count"] == 1


def test_prepare_for_ragflow_appends_table_index_after_normalization(monkeypatch):
    monkeypatch.setattr(
        parser_router,
        "get_settings",
        lambda: _settings(
            mineru_enabled=False,
            table_row_index_enabled=True,
            table_row_index_max_rows=5000,
        ),
    )

    csv_data = b"province,city,season,amount\nGuangxi,Guilin,Jan-Feb,1040\n"

    name, data, content_type, metadata = parser_router.prepare_for_ragflow(
        "doc-2",
        "policy.csv",
        csv_data,
        "text/csv",
    )

    text = data.decode("utf-8")
    assert name == "policy.csv.ragflow.md"
    assert content_type == "text/markdown"
    assert "city=Guilin" in text
    assert "amount=1040" in text
    assert metadata["table_index"]["table_row_count"] == 1
