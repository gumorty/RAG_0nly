import io
import json
import mimetypes
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings


class MinerUError(RuntimeError):
    pass


@dataclass
class MinerUParseResult:
    markdown: str
    metadata: dict[str, Any]
    artifacts: list[dict[str, Any]] = field(default_factory=list)


class MinerUClient:
    """MinerU precise parsing API client.

    Uses the token-protected batch local-file flow:
    1. request signed upload URL
    2. PUT file bytes
    3. poll batch result
    4. download result zip and extract full.md
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.mineru_base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {self.settings.mineru_api_key}",
            "Content-Type": "application/json",
            "Accept": "*/*",
        }

    def enabled(self) -> bool:
        return bool(self.settings.mineru_enabled and self.settings.mineru_api_key.strip())

    def parse_file(self, filename: str, data: bytes, data_id: str | None = None) -> MinerUParseResult:
        if not self.enabled():
            raise MinerUError("MinerU is not enabled or API key is missing")
        if len(data) > self.settings.mineru_max_file_size_mb * 1024 * 1024:
            raise MinerUError("File exceeds MinerU configured size limit")

        model_version = self._model_version(filename)
        request_body = {
            "files": [
                {
                    "name": filename,
                    "data_id": data_id or Path(filename).stem[:120],
                    "is_ocr": self._needs_ocr(filename, data),
                }
            ],
            "model_version": model_version,
            "language": self.settings.mineru_language,
            "enable_table": True,
            "enable_formula": True,
        }
        created = self._request("POST", "/api/v4/file-urls/batch", json=request_body)
        payload = created.get("data") or {}
        batch_id = payload.get("batch_id")
        upload_urls = payload.get("file_urls") or []
        if not batch_id or not upload_urls:
            raise MinerUError("MinerU did not return batch_id and upload URL")

        try:
            with httpx.Client(timeout=180) as client:
                put_response = client.put(str(upload_urls[0]), content=data)
                put_response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MinerUError(f"MinerU upload failed: {exc}") from exc

        result = self._wait_for_batch(str(batch_id))
        zip_url = result.get("full_zip_url")
        if not zip_url:
            raise MinerUError("MinerU completed without full_zip_url")
        markdown, artifact_metadata, artifacts = self._download_full_markdown(str(zip_url))
        return MinerUParseResult(
            markdown=markdown,
            metadata={
                "mineru_batch_id": batch_id,
                "mineru_state": result.get("state"),
                "mineru_model_version": model_version,
                "mineru_result_zip_available": True,
                **artifact_metadata,
                "mineru_trace": _safe_trace(result),
            },
            artifacts=artifacts,
        )

    def _wait_for_batch(self, batch_id: str) -> dict:
        deadline = time.monotonic() + self.settings.mineru_timeout_seconds
        last_result: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            response = self._request("GET", f"/api/v4/extract-results/batch/{batch_id}")
            data = response.get("data") or {}
            results = data.get("extract_result") or []
            if results:
                last_result = dict(results[0])
                state = str(last_result.get("state") or "").lower()
                if state == "done":
                    return last_result
                if state == "failed":
                    raise MinerUError(str(last_result.get("err_msg") or "MinerU parsing failed"))
            time.sleep(self.settings.mineru_poll_interval_seconds)
        raise MinerUError(f"MinerU parsing timed out; last_result={last_result}")

    def _download_full_markdown(self, zip_url: str) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        try:
            with httpx.Client(timeout=180, follow_redirects=True) as client:
                response = client.get(zip_url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MinerUError(f"MinerU result zip download failed: {exc}") from exc

        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                names = archive.namelist()
                preferred = [name for name in names if name.endswith("full.md")]
                markdown_name = preferred[0] if preferred else next((name for name in names if name.endswith(".md")), None)
                if not markdown_name:
                    raise MinerUError("MinerU result zip does not contain Markdown")
                metadata = _artifact_metadata(names, markdown_name)
                artifacts = _extract_indexable_artifacts(archive, names, self.settings)
                return archive.read(markdown_name).decode("utf-8", errors="replace"), metadata, artifacts
        except zipfile.BadZipFile as exc:
            raise MinerUError("MinerU result zip is invalid") from exc
        except KeyError as exc:
            raise MinerUError("MinerU result Markdown is missing from zip") from exc

    def _model_version(self, filename: str) -> str:
        if Path(filename).suffix.lower() in {".html", ".htm"}:
            return "MinerU-HTML"
        return self.settings.mineru_model_version or "vlm"

    def _needs_ocr(self, filename: str, data: bytes | None = None) -> bool:
        ext = Path(filename).suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}:
            return True
        if ext == ".pdf":
            mode = self.settings.mineru_pdf_ocr_mode
            if mode == "always":
                return True
            if mode == "never":
                return False
            if data:
                from app.services.pdf_probe import probe_pdf_text_layer

                return probe_pdf_text_layer(data).likely_scanned
        return False

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=180) as client:
                response = client.request(method, url, headers=self.headers, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise MinerUError(f"MinerU request failed {method} {path}: {exc}") from exc
        except ValueError as exc:
            raise MinerUError(f"MinerU returned non-JSON response for {method} {path}") from exc
        if payload.get("code") not in (0, None):
            raise MinerUError(str(payload.get("msg") or payload))
        return payload


def _safe_trace(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "full_zip_url"}


def _artifact_metadata(names: list[str], markdown_name: str) -> dict[str, Any]:
    image_exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")
    json_files = [name for name in names if name.lower().endswith(".json")]
    image_files = [name for name in names if name.lower().endswith(image_exts)]
    table_files = [
        name for name in names
        if any(marker in name.lower() for marker in ("table", "tables", "layout", "middle"))
    ]
    return {
        "mineru_markdown_file": markdown_name,
        "mineru_artifact_count": len(names),
        "mineru_image_count": len(image_files),
        "mineru_json_count": len(json_files),
        "mineru_table_artifact_count": len(table_files),
        "mineru_image_files_sample": image_files[:20],
        "mineru_json_files_sample": json_files[:20],
        "mineru_table_files_sample": table_files[:20],
    }


def _extract_indexable_artifacts(archive: zipfile.ZipFile, names: list[str], settings: Any) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    max_images = int(getattr(settings, "visual_asset_max_images", 40) or 0)
    max_image_bytes = int(getattr(settings, "visual_asset_max_image_mb", 8) or 8) * 1024 * 1024
    max_json_chars = int(getattr(settings, "visual_asset_max_json_chars", 120000) or 120000)
    image_exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")
    image_count = 0
    for name in names:
        lower = name.lower()
        if lower.endswith(image_exts):
            if image_count >= max_images:
                continue
            info = archive.getinfo(name)
            if info.file_size > max_image_bytes:
                continue
            data = archive.read(name)
            artifacts.append(
                {
                    "kind": "image",
                    "name": name,
                    "filename": Path(name).name,
                    "content_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
                    "size": len(data),
                    "data": data,
                }
            )
            image_count += 1
            continue
        if lower.endswith(".json"):
            try:
                raw = archive.read(name)
                text = raw.decode("utf-8", errors="replace")
                snippet = _json_text_snippet(text, max_chars=max_json_chars)
            except Exception:
                snippet = ""
            if snippet:
                artifacts.append(
                    {
                        "kind": "json",
                        "name": name,
                        "filename": Path(name).name,
                        "content_type": "application/json",
                        "size": len(snippet.encode("utf-8", errors="ignore")),
                        "text": snippet,
                    }
                )
    return artifacts


def _json_text_snippet(text: str, max_chars: int) -> str:
    try:
        payload = json.loads(text)
    except ValueError:
        return text[:max_chars]
    strings: list[str] = []

    def walk(value: Any) -> None:
        if len(" ".join(strings)) >= max_chars:
            return
        if isinstance(value, str):
            cleaned = " ".join(value.split())
            if len(cleaned) >= 2:
                strings.append(cleaned)
        elif isinstance(value, list):
            for item in value[:2000]:
                walk(item)
        elif isinstance(value, dict):
            for key, item in list(value.items())[:2000]:
                if isinstance(key, str) and key in {"text", "content", "caption", "html", "latex", "type", "page", "page_no"}:
                    walk(item)
                elif key in {"blocks", "lines", "spans", "tables", "figures", "images", "cells"}:
                    walk(item)

    walk(payload)
    return "\n".join(_dedupe(strings))[:max_chars]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
