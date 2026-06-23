"""Auto-select RAGFlow chunk method by file type.

Each chunk method maps to a RAGFlow app-specific parser:
  - paper:  academic paper (PDF) — layout recognition + OCR + outline
  - naive:  general text/document — fallback
  - table:  spreadsheet (XLSX/CSV) — per-row chunking + column type detection
  - presentation: slide deck (PPTX) — one chunk per slide
  - picture: image (JPG/PNG) — OCR + optional VLM description
  - one:    single-chunk for the entire file
  - email:  email (EML/MSG) — header/body/attachment extraction
  - audio:  audio file — ASR transcription
  - book:   long document with TOC — hierarchical merge
  - manual: operation manual — PDF outline-based section merge
  - laws:   legal document — tree-based hierarchical merge
  - qa:     Q&A pairs — one pair per chunk
  - resume: CV/resume — multi-phase LLM extraction
  - tag:    tag/classification rows
"""

from pathlib import Path

# Mapping: file extension (lowercase) → RAGFlow chunk_method.
_FILE_TO_CHUNK_METHOD: dict[str, str] = {
    # PDF — use paper parser for layout-aware parsing
    ".pdf": "paper",
    # Word documents
    ".docx": "naive",
    ".doc": "naive",
    # Spreadsheets — use table parser for structured data
    ".xlsx": "table",
    ".xls": "table",
    ".csv": "table",
    # Presentations
    ".pptx": "presentation",
    ".ppt": "presentation",
    # Markup / text
    ".md": "naive",
    ".markdown": "naive",
    ".mdx": "naive",
    ".txt": "naive",
    ".html": "naive",
    ".htm": "naive",
    # Images — OCR + optional VLM
    ".jpg": "picture",
    ".jpeg": "picture",
    ".png": "picture",
    ".gif": "picture",
    ".bmp": "picture",
    ".tiff": "picture",
    ".webp": "picture",
    # JSON / XML — single-chunk
    ".json": "one",
    ".jsonl": "one",
    ".xml": "naive",
    # Email
    ".eml": "email",
    ".msg": "email",
    # Audio — ASR
    ".mp3": "audio",
    ".wav": "audio",
    ".aac": "audio",
    ".flac": "audio",
    ".ogg": "audio",
    ".aiff": "audio",
    # Code — naive is fine for retrieval
    ".py": "naive",
    ".js": "naive",
    ".java": "naive",
    ".go": "naive",
    ".ts": "naive",
    ".sh": "naive",
    ".sql": "naive",
    ".c": "naive",
    ".cpp": "naive",
    ".h": "naive",
    ".php": "naive",
    ".cs": "naive",
    ".kt": "naive",
    # EPUB
    ".epub": "naive",
}

# Mapping: content-type prefix → chunk_method (used when extension is unknown).
_CONTENT_TYPE_TO_CHUNK_METHOD: dict[str, str] = {
    "application/pdf": "paper",
    "application/vnd.openxmlformats-officedocument.wordprocessingml": "naive",
    "application/msword": "naive",
    "application/vnd.openxmlformats-officedocument.spreadsheetml": "table",
    "application/vnd.ms-excel": "table",
    "application/vnd.openxmlformats-officedocument.presentationml": "presentation",
    "application/vnd.ms-powerpoint": "presentation",
    "text/": "naive",
    "image/": "picture",
    "message/": "email",
    "audio/": "audio",
}


def select_chunk_method(filename: str, content_type: str | None = None) -> str:
    """Select the best RAGFlow chunk method for a given file.

    Args:
        filename: The document filename (may include path).
        content_type: Optional MIME type for fallback detection.

    Returns:
        A RAGFlow chunk_method string (e.g. "paper", "table", "naive").
    """
    ext = Path(filename).suffix.lower()
    if ext in _FILE_TO_CHUNK_METHOD:
        return _FILE_TO_CHUNK_METHOD[ext]
    if content_type:
        for prefix, method in _CONTENT_TYPE_TO_CHUNK_METHOD.items():
            if content_type.startswith(prefix):
                return method
    return "naive"


def supports_chunk_method_override(chunk_method: str) -> bool:
    """Return True if RAGFlow supports per-document chunk_method override.

    RAGFlow's PATCH /datasets/{id}/documents/{doc_id} allows overriding
    chunk_method and parser_config per document for most methods.
    """
    return chunk_method in (
        "naive", "paper", "manual", "laws", "table", "qa",
        "resume", "book", "presentation", "picture", "one",
        "email", "audio", "tag",
    )
