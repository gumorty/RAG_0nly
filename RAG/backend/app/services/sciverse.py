from typing import Any

import httpx

from app.core.config import get_settings


class SciVerseError(RuntimeError):
    pass


class SciVerseClient:
    """Backend-only SciVerse connector.

    SciVerse is a scientific literature evidence source, not a general parser
    for internal Office/PDF files. Keep it disabled by default and use it later
    for paper metadata, citable chunks, and full-text literature enrichment.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.sciverse_base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {self.settings.sciverse_api_key}", "Content-Type": "application/json"}

    def enabled(self) -> bool:
        return bool(self.settings.sciverse_enabled and self.settings.sciverse_api_key.strip())

    def agentic_search(self, query: str, **options: Any) -> dict:
        if not self.enabled():
            raise SciVerseError("SciVerse is not enabled or API key is missing")
        return self._request("POST", "/api/sciverse/agentic-search", json={"query": query, **options})

    def meta_search(self, query: str, **options: Any) -> dict:
        if not self.enabled():
            raise SciVerseError("SciVerse is not enabled or API key is missing")
        return self._request("POST", "/api/sciverse/meta-search", json={"query": query, **options})

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=120) as client:
                response = client.request(method, url, headers=self.headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise SciVerseError(f"SciVerse request failed {method} {path}: {exc}") from exc
        except ValueError as exc:
            raise SciVerseError(f"SciVerse returned non-JSON response for {method} {path}") from exc
