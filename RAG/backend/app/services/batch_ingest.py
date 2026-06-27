import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePosixPath


SUPPORTED_SUFFIXES = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".xlsm",
    ".html",
    ".htm",
    ".md",
    ".markdown",
    ".txt",
    ".csv",
    ".json",
}


@dataclass
class ZipEntry:
    filename: str
    data: bytes
    content_type: str


@dataclass
class ZipReadResult:
    entries: list[ZipEntry] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)


def read_zip_documents(data: bytes, max_files: int = 200, max_file_bytes: int = 50 * 1024 * 1024) -> ZipReadResult:
    result = ZipReadResult()
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            path = PurePosixPath(info.filename)
            skip_reason = _system_path_reason(path)
            if skip_reason:
                result.skipped.append({"filename": info.filename, "reason": skip_reason})
                continue
            suffix = path.suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES:
                result.skipped.append({"filename": info.filename, "reason": "unsupported_extension"})
                continue
            if info.file_size > max_file_bytes:
                result.skipped.append({"filename": info.filename, "reason": "file_too_large"})
                continue
            if len(result.entries) >= max_files:
                result.skipped.append({"filename": info.filename, "reason": "max_files_exceeded"})
                continue
            try:
                result.entries.append(
                    ZipEntry(
                        filename=path.name,
                        data=archive.read(info),
                        content_type=_content_type_for_suffix(suffix),
                    )
                )
            except Exception as exc:
                result.failed.append({"filename": info.filename, "reason": str(exc)})
    return result


def _is_system_path(path: PurePosixPath) -> bool:
    return _system_path_reason(path) is not None


def _system_path_reason(path: PurePosixPath) -> str | None:
    system_names = {"__MACOSX", ".DS_Store", "Thumbs.db"}
    if any(part.startswith(".") for part in path.parts):
        return "hidden_path"
    if any(part in system_names for part in path.parts):
        return "system_path"
    return None


def _content_type_for_suffix(suffix: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
        ".html": "text/html",
        ".htm": "text/html",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
        ".csv": "text/csv",
        ".json": "application/json",
    }.get(suffix, "application/octet-stream")
