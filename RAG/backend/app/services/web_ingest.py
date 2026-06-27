import re
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings


class WebIngestionError(ValueError):
    pass


async def fetch_web_document(url: str) -> tuple[str, bytes, str]:
    _validate_fetch_url(url)
    parsed = urlparse(url)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "EnterpriseRAGBot/0.1"})
        response.raise_for_status()
    _validate_fetch_url(str(response.url))
    content_type = response.headers.get("content-type", "text/html").split(";")[0]
    if "html" not in content_type and "text" not in content_type:
        raise WebIngestionError(f"Unsupported URL content type: {content_type}")
    title = _extract_title(response.text) or parsed.netloc
    filename = _safe_filename(title) + ".html"
    return filename, response.content, content_type


def _validate_fetch_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebIngestionError("Only valid http(s) URLs are supported")
    if parsed.username or parsed.password:
        raise WebIngestionError("URLs with embedded credentials are not allowed")
    if get_settings().allow_private_url_ingest:
        return
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebIngestionError(f"Could not resolve URL host: {parsed.hostname}") from exc
    for item in addresses:
        host = item[4][0]
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise WebIngestionError("Private, loopback, link-local, multicast, or reserved URL targets are not allowed")


def _extract_title(html: str) -> str | None:
    parser = _TitleParser()
    parser.feed(html)
    return parser.title or parser.h1


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", value).strip("-._")
    return cleaned[:120] or "web-document"


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._active_tag: str | None = None
        self._title_parts: list[str] = []
        self._h1_parts: list[str] = []

    @property
    def title(self) -> str | None:
        value = " ".join(part.strip() for part in self._title_parts if part.strip()).strip()
        return value or None

    @property
    def h1(self) -> str | None:
        value = " ".join(part.strip() for part in self._h1_parts if part.strip()).strip()
        return value or None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"title", "h1"}:
            self._active_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self._active_tag:
            self._active_tag = None

    def handle_data(self, data: str) -> None:
        if self._active_tag == "title":
            self._title_parts.append(data)
        elif self._active_tag == "h1" and not self.title:
            self._h1_parts.append(data)
