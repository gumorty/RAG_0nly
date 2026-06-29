from pathlib import Path

from app.core.config import get_settings
from app.services.document_normalize import normalize_for_ragflow
from app.services.document_quality import score_ingest_candidate, score_parsed_text
from app.services.mineru import MinerUClient, MinerUError
from app.services.table_index import augment_markdown_with_table_index


MINERU_CANDIDATE_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff",
    ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
}


def prepare_for_ragflow(
    document_id: str,
    filename: str,
    data: bytes,
    content_type: str | None,
) -> tuple[str, bytes, str | None, dict]:
    """Prepare a file for RAGFlow with optional MinerU pre-parsing.

    The returned metadata is safe to store in Document.metadata.
    """
    decision = _select_parser_engine(filename, data, content_type)
    raw_quality = score_ingest_candidate(filename, data, content_type, decision["engine"])

    if decision["engine"] == "mineru_precise":
        try:
            result = MinerUClient().parse_file(filename, data, data_id=document_id)
            markdown_data, table_index_meta = _augment_markdown_if_enabled(result.markdown.encode("utf-8"))
            markdown = markdown_data.decode("utf-8", errors="replace")
            parsed_quality = score_parsed_text(markdown, "mineru_precise")
            metadata = {
                "parser_engine": "mineru_precise",
                "parser_decision": decision,
                "raw_quality": raw_quality,
                "parsed_quality": parsed_quality,
                "normalized_for_ragflow": True,
                "normalizer": "mineru",
                "table_index": table_index_meta,
                **result.metadata,
            }
            return f"{filename}.mineru.md", markdown_data, "text/markdown", metadata
        except MinerUError as exc:
            fallback_name, fallback_data, fallback_type, fallback_meta = normalize_for_ragflow(filename, data, content_type)
            table_index_meta: dict = {}
            if fallback_type == "text/markdown":
                fallback_data, table_index_meta = _augment_markdown_if_enabled(fallback_data)
            metadata = {
                "parser_engine": "ragflow_fallback_after_mineru",
                "parser_decision": decision,
                "raw_quality": raw_quality,
                "parser_warning": str(exc),
                "table_index": table_index_meta,
                **fallback_meta,
            }
            if fallback_type == "text/markdown":
                metadata["parsed_quality"] = score_parsed_text(fallback_data.decode("utf-8", errors="replace"), metadata["parser_engine"])
            return fallback_name, fallback_data, fallback_type, metadata

    normalized_name, normalized_data, normalized_type, normalization = normalize_for_ragflow(filename, data, content_type)
    parser_engine = "ragflow_normalized" if normalization.get("normalized_for_ragflow") else "ragflow_deepdoc"
    metadata = {
        "parser_engine": parser_engine,
        "parser_decision": decision,
        "raw_quality": raw_quality,
        **normalization,
    }
    if normalized_type == "text/markdown":
        normalized_data, table_index_meta = _augment_markdown_if_enabled(normalized_data)
        metadata["table_index"] = table_index_meta
        metadata["parsed_quality"] = score_parsed_text(normalized_data.decode("utf-8", errors="replace"), parser_engine)
    return normalized_name, normalized_data, normalized_type, metadata


def _augment_markdown_if_enabled(data: bytes) -> tuple[bytes, dict]:
    settings = get_settings()
    if not getattr(settings, "table_row_index_enabled", False):
        return data, {}
    markdown = data.decode("utf-8", errors="replace")
    indexed = augment_markdown_with_table_index(
        markdown,
        max_rows=getattr(settings, "table_row_index_max_rows", 5000),
    )
    return indexed.markdown.encode("utf-8"), indexed.metadata


def _select_parser_engine(filename: str, data: bytes, content_type: str | None) -> dict:
    settings = get_settings()
    ext = Path(filename).suffix.lower()
    if not settings.mineru_enabled:
        return {"engine": "ragflow_deepdoc", "reason": "mineru_disabled", "extension": ext}
    if not settings.mineru_api_key.strip():
        return {"engine": "ragflow_deepdoc", "reason": "mineru_api_key_missing", "extension": ext}
    if len(data) > settings.mineru_max_file_size_mb * 1024 * 1024:
        return {"engine": "ragflow_deepdoc", "reason": "mineru_size_limit", "extension": ext}
    if ext in MINERU_CANDIDATE_EXTENSIONS:
        return {"engine": "mineru_precise", "reason": "complex_document_type", "extension": ext}
    if content_type and (content_type.startswith("image/") or content_type == "application/pdf"):
        return {"engine": "mineru_precise", "reason": "complex_content_type", "extension": ext}
    return {"engine": "ragflow_deepdoc", "reason": "not_mineru_candidate", "extension": ext}
