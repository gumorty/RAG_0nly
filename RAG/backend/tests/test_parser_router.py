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
