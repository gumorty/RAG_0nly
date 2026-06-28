import io
import time
import zipfile
from dataclasses import dataclass
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
                    "is_ocr": self._needs_ocr(filename),
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

        with httpx.Client(timeout=180) as client:
            put_response = client.put(str(upload_urls[0]), content=data)
            put_response.raise_for_status()

        result = self._wait_for_batch(str(batch_id))
        zip_url = result.get("full_zip_url")
        if not zip_url:
            raise MinerUError("MinerU completed without full_zip_url")
        markdown = self._download_full_markdown(str(zip_url))
        return MinerUParseResult(
            markdown=markdown,
            metadata={
                "mineru_batch_id": batch_id,
                "mineru_state": result.get("state"),
                "mineru_model_version": model_version,
                "mineru_result_zip_available": True,
                "mineru_trace": _safe_trace(result),
            },
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

    def _download_full_markdown(self, zip_url: str) -> str:
        with httpx.Client(timeout=180, follow_redirects=True) as client:
            response = client.get(zip_url)
            response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            preferred = [name for name in names if name.endswith("full.md")]
            markdown_name = preferred[0] if preferred else next((name for name in names if name.endswith(".md")), None)
            if not markdown_name:
                raise MinerUError("MinerU result zip does not contain Markdown")
            return archive.read(markdown_name).decode("utf-8", errors="replace")

    def _model_version(self, filename: str) -> str:
        if Path(filename).suffix.lower() in {".html", ".htm"}:
            return "MinerU-HTML"
        return self.settings.mineru_model_version or "vlm"

    def _needs_ocr(self, filename: str) -> bool:
        return Path(filename).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}

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
