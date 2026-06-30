from pathlib import Path

from app.services.pdf_probe import probe_pdf_text_layer


TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".html", ".htm", ".py", ".js", ".ts", ".java", ".go", ".sql"}
STRUCTURED_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}


def score_ingest_candidate(filename: str, data: bytes, content_type: str | None, parser_engine: str) -> dict:
    """Score raw input risk before it is sent to RAGFlow.

    This is not answer quality. It is an operational signal that tells us whether
    a document is likely to need stronger parsing, OCR, or manual review.
    """
    ext = Path(filename).suffix.lower()
    size = len(data or b"")
    warnings: list[str] = []
    risk = 0.0

    if size == 0:
        warnings.append("empty_file")
        risk += 1.0
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}:
        warnings.append("image_or_scan_requires_ocr")
        risk += 0.35
    if ext == ".pdf":
        warnings.append("pdf_layout_needs_verification")
        risk += 0.2
        probe = probe_pdf_text_layer(data)
        if probe.likely_scanned:
            warnings.append("pdf_likely_scanned_requires_ocr")
            risk += 0.45
        if probe.encrypted:
            warnings.append("pdf_encrypted_or_restricted")
            risk += 0.4
        if probe.error and probe.error != "pdf_encrypted":
            warnings.append("pdf_text_layer_probe_failed")
            risk += 0.15
    if ext in {".xls", ".xlsx", ".csv"}:
        warnings.append("table_structure_needs_verification")
        risk += 0.15
    if ext not in TEXT_EXTENSIONS | STRUCTURED_EXTENSIONS:
        warnings.append("unknown_extension")
        risk += 0.2

    if ext in TEXT_EXTENSIONS:
        text = data.decode("utf-8", errors="replace")
        replacement_ratio = text.count("\ufffd") / max(len(text), 1)
        if replacement_ratio > 0.01:
            warnings.append("possible_encoding_damage")
            risk += min(replacement_ratio * 8, 0.35)
        if len(text.strip()) < 80:
            warnings.append("very_short_text")
            risk += 0.25

    if parser_engine.startswith("mineru") and "pdf_likely_scanned_requires_ocr" not in warnings:
        risk = max(0.0, risk - 0.15)

    score = max(0.0, min(1.0, 1.0 - risk))
    return {
        "score": round(score, 3),
        "parser_engine": parser_engine,
        "file_size_bytes": size,
        "extension": ext,
        "content_type": content_type,
        "warnings": sorted(set(warnings)),
    }


def score_parsed_text(text: str, parser_engine: str) -> dict:
    """Score parsed text/Markdown before it enters RAGFlow indexing."""
    value = text or ""
    stripped = value.strip()
    warnings: list[str] = []
    risk = 0.0

    if not stripped:
        warnings.append("empty_parsed_text")
        risk += 1.0
    if len(stripped) < 120:
        warnings.append("parsed_text_too_short")
        risk += 0.3
    replacement_ratio = value.count("\ufffd") / max(len(value), 1)
    if replacement_ratio > 0.005:
        warnings.append("garbled_characters")
        risk += min(replacement_ratio * 10, 0.4)
    lines = [line for line in value.splitlines() if line.strip()]
    table_lines = [line for line in lines if "|" in line]
    if table_lines and len(table_lines) < 3:
        warnings.append("table_may_be_incomplete")
        risk += 0.15

    score = max(0.0, min(1.0, 1.0 - risk))
    return {
        "score": round(score, 3),
        "parser_engine": parser_engine,
        "char_count": len(value),
        "line_count": len(lines),
        "table_line_count": len(table_lines),
        "warnings": sorted(set(warnings)),
    }
