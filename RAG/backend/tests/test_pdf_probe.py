from pathlib import Path
from types import SimpleNamespace

from app.services import mineru
from app.services.document_quality import score_ingest_candidate
from app.services.pdf_probe import probe_pdf_text_layer

FIXTURES = Path(__file__).parent / "fixtures"


def test_scanned_pdf_is_detected_as_ocr_candidate():
    data = (FIXTURES / "scan_policy.pdf").read_bytes()

    result = probe_pdf_text_layer(data)

    assert result.page_count == 4
    assert result.likely_scanned is True
    assert result.text_char_count == 0


def test_text_table_pdf_is_not_marked_as_scanned():
    data = (FIXTURES / "table_policy.pdf").read_bytes()

    result = probe_pdf_text_layer(data)

    assert result.page_count == 3
    assert result.likely_scanned is False
    assert result.text_char_count > 1000


def test_mineru_ocr_auto_detects_scanned_pdf():
    client = mineru.MinerUClient.__new__(mineru.MinerUClient)
    client.settings = SimpleNamespace(mineru_pdf_ocr_mode="auto")
    data = (FIXTURES / "scan_policy.pdf").read_bytes()

    assert client._needs_ocr("scan_policy.pdf", data) is True


def test_mineru_ocr_auto_skips_text_pdf():
    client = mineru.MinerUClient.__new__(mineru.MinerUClient)
    client.settings = SimpleNamespace(mineru_pdf_ocr_mode="auto")
    data = (FIXTURES / "table_policy.pdf").read_bytes()

    assert client._needs_ocr("table_policy.pdf", data) is False


def test_raw_quality_marks_scanned_pdf_as_ocr_risk():
    data = (FIXTURES / "scan_policy.pdf").read_bytes()

    score = score_ingest_candidate("scan_policy.pdf", data, "application/pdf", "mineru_precise")

    assert score["score"] < 0.7
    assert "pdf_likely_scanned_requires_ocr" in score["warnings"]
