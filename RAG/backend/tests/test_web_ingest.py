from app.services.web_ingest import _extract_title, _safe_filename


def test_extract_title_prefers_html_title() -> None:
    html = "<html><head><title>Project Report</title></head><body><h1>Fallback</h1></body></html>"
    assert _extract_title(html) == "Project Report"


def test_extract_title_falls_back_to_h1() -> None:
    html = "<html><body><h1>Weekly Meeting</h1></body></html>"
    assert _extract_title(html) == "Weekly Meeting"


def test_safe_filename_removes_unsafe_characters() -> None:
    assert _safe_filename("RAG / Project: Weekly? Report") == "RAG-Project-Weekly-Report"
