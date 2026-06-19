from io import BytesIO
from zipfile import ZipFile

from app.services.batch_ingest import read_zip_documents


def test_read_zip_documents_imports_supported_files_and_skips_unsupported() -> None:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("weekly/report.md", "# Weekly\nDone")
        archive.writestr("notes.tmp", "skip")
        archive.writestr(".hidden/secret.md", "skip")

    result = read_zip_documents(buffer.getvalue())

    assert len(result.entries) == 1
    assert result.entries[0].filename == "report.md"
    assert result.entries[0].content_type == "text/markdown"
    reasons = {item["reason"] for item in result.skipped}
    assert "unsupported_extension" in reasons
    assert "hidden_path" in reasons
