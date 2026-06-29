from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from statistics import mean
from typing import Any

from pypdf import PdfReader


@dataclass(frozen=True)
class PdfProbeResult:
    """Lightweight PDF preflight result used before parser routing.

    The probe intentionally relies on the PDF text layer only.  It does not run
    OCR.  A PDF with almost no extractable text is treated as likely scanned so
    the downstream precise parser can enable OCR.
    """

    page_count: int
    checked_pages: int
    text_char_count: int
    avg_text_chars_per_page: float
    min_text_chars_per_page: int
    likely_scanned: bool
    encrypted: bool
    error: str | None = None

    def as_metadata(self) -> dict[str, Any]:
        return {
            "page_count": self.page_count,
            "checked_pages": self.checked_pages,
            "text_char_count": self.text_char_count,
            "avg_text_chars_per_page": round(self.avg_text_chars_per_page, 2),
            "min_text_chars_per_page": self.min_text_chars_per_page,
            "likely_scanned": self.likely_scanned,
            "encrypted": self.encrypted,
            "error": self.error,
        }


def probe_pdf_text_layer(data: bytes, max_pages: int = 5, scanned_threshold: int = 30) -> PdfProbeResult:
    """Detect whether a PDF probably needs OCR.

    Rules:
    - If the file cannot be read, treat it as high risk / likely scanned.
    - If sampled pages have fewer than ``scanned_threshold`` extractable
      characters on average, treat it as likely scanned.
    - We only sample early pages for speed because this function runs in the
      upload path.
    """

    try:
        reader = PdfReader(BytesIO(data or b""))
        encrypted = bool(getattr(reader, "is_encrypted", False))
        if encrypted:
            return PdfProbeResult(
                page_count=0,
                checked_pages=0,
                text_char_count=0,
                avg_text_chars_per_page=0.0,
                min_text_chars_per_page=0,
                likely_scanned=True,
                encrypted=True,
                error="pdf_encrypted",
            )

        page_count = len(reader.pages)
        sample_count = min(max(page_count, 0), max_pages)
        if sample_count <= 0:
            return PdfProbeResult(
                page_count=page_count,
                checked_pages=0,
                text_char_count=0,
                avg_text_chars_per_page=0.0,
                min_text_chars_per_page=0,
                likely_scanned=True,
                encrypted=False,
                error="pdf_has_no_pages",
            )

        char_counts: list[int] = []
        for page in reader.pages[:sample_count]:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            char_counts.append(len(text.strip()))

        total_chars = sum(char_counts)
        avg_chars = mean(char_counts) if char_counts else 0.0
        min_chars = min(char_counts) if char_counts else 0
        return PdfProbeResult(
            page_count=page_count,
            checked_pages=sample_count,
            text_char_count=total_chars,
            avg_text_chars_per_page=float(avg_chars),
            min_text_chars_per_page=min_chars,
            likely_scanned=avg_chars < scanned_threshold,
            encrypted=False,
        )
    except Exception as exc:
        return PdfProbeResult(
            page_count=0,
            checked_pages=0,
            text_char_count=0,
            avg_text_chars_per_page=0.0,
            min_text_chars_per_page=0,
            likely_scanned=True,
            encrypted=False,
            error=str(exc),
        )
