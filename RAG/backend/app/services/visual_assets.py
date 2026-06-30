from __future__ import annotations

import base64
import hashlib
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Document, VisualAssetCache
from app.rag.normalize import normalize_text, sha256_text
from app.rag.schemas import RetrievedChunk
from app.services.storage import ObjectStorage


def augment_markdown_with_visual_assets(markdown: str, artifacts: list[dict[str, Any]]) -> tuple[str, dict, list[dict]]:
    settings = get_settings()
    if not getattr(settings, "visual_asset_index_enabled", True):
        return markdown, {"visual_asset_index_enabled": False}, []
    text = markdown or ""
    image_assets = [_build_image_payload(text, item) for item in artifacts if item.get("kind") == "image"]
    image_assets = [item for item in image_assets if item]
    json_sections = [_build_json_section(item) for item in artifacts if item.get("kind") == "json" and item.get("text")]

    if not image_assets and not json_sections:
        return text, {"visual_asset_index_enabled": True, "visual_asset_count": 0, "visual_layout_text_count": 0}, []

    lines = [
        "",
        "",
        "# 视觉资产检索索引",
        "",
        "以下内容来自 MinerU 结果包中的图片、扫描页、版面 JSON 和表格/图像结构化线索，用于图片、扫描件、图表和页面定位类问题召回。",
    ]
    for asset in image_assets:
        lines.extend(
            [
                "",
                f"## 视觉资产 {asset['asset_id']}",
                f"- 文件：{asset['source_path']}",
                f"- 类型：{asset['asset_type']}",
            ]
        )
        if asset.get("page_no"):
            lines.append(f"- 页码线索：第 {asset['page_no']} 页")
        if asset.get("figure_no"):
            lines.append(f"- 图号线索：{asset['figure_no']}")
        if asset.get("caption"):
            lines.append(f"- 图题/标题：{asset['caption']}")
        if asset.get("context"):
            lines.append(f"- 邻近文本：{asset['context']}")
        lines.append("- 检索关键词：图片 扫描件 OCR 图表 图中 页面 视觉资产 原文定位")

    if json_sections:
        lines.extend(["", "## MinerU 版面与结构化文本"])
        lines.extend(json_sections[:20])

    metadata = {
        "visual_asset_index_enabled": True,
        "visual_asset_count": len(image_assets),
        "visual_layout_text_count": len(json_sections),
        "visual_asset_ids": [item["asset_id"] for item in image_assets[:50]],
    }
    return text + "\n".join(lines), metadata, image_assets


def sync_visual_assets(db: Session, document: Document, assets: list[dict]) -> int:
    if not assets:
        return 0
    settings = get_settings()
    storage = ObjectStorage()
    embedder = VisualEmbeddingClient() if _visual_embedding_available(settings) else None
    synced = 0
    seen: set[str] = set()
    for asset in assets:
        asset_id = str(asset.get("asset_id") or "")
        if not asset_id:
            continue
        seen.add(asset_id)
        row = db.scalar(
            select(VisualAssetCache).where(
                VisualAssetCache.document_id == document.id,
                VisualAssetCache.asset_id == asset_id,
            )
        )
        if not row:
            row = VisualAssetCache(
                collection_id=document.collection_id,
                document_id=document.id,
                asset_id=asset_id,
                asset_type=str(asset.get("asset_type") or "image"),
            )
            db.add(row)
        row.collection_id = document.collection_id
        row.filename = str(asset.get("filename") or "")
        row.source_path = str(asset.get("source_path") or "")
        row.page_no = asset.get("page_no")
        row.figure_no = asset.get("figure_no")
        row.caption = asset.get("caption")
        row.context = asset.get("context")
        row.available = True
        row.updated_at = datetime.utcnow()
        image_bytes = _decode_asset_data(asset)
        object_key = row.object_key
        if image_bytes and not object_key:
            object_key = f"{document.collection_id}/{document.id}/visual/{asset_id}-{_safe_filename(row.filename or 'image')}"
            storage.put_bytes(object_key, image_bytes, str(asset.get("content_type") or "application/octet-stream"))
        row.object_key = object_key
        embedding_error = None
        if embedder and image_bytes:
            try:
                row.embedding = embedder.embed_image(
                    image_bytes,
                    str(asset.get("content_type") or "image/png"),
                    context=_asset_text(asset),
                )
                row.embedding_model = settings.visual_embedding_model
            except Exception as exc:
                embedding_error = str(exc)[:500]
        row.metadata_ = {
            "content_type": asset.get("content_type"),
            "size": asset.get("size"),
            "source": "mineru_zip",
            "text_hash": sha256_text(normalize_text(_asset_text(asset))),
            "embedding_enabled": bool(embedder),
            "embedding_error": embedding_error,
        }
        synced += 1
    if seen:
        db.query(VisualAssetCache).filter(
            VisualAssetCache.document_id == document.id,
            ~VisualAssetCache.asset_id.in_(seen),
        ).update({VisualAssetCache.available: False}, synchronize_session=False)
    return synced


def local_visual_search(
    db: Session,
    *,
    collection_id: str,
    query: str,
    top_k: int = 8,
    acl_principals: list[str] | None = None,
) -> list[RetrievedChunk]:
    settings = get_settings()
    if not _visual_embedding_available(settings):
        return []
    if not _looks_like_visual_query(query):
        return []
    rows = db.scalars(
        select(VisualAssetCache)
        .where(
            VisualAssetCache.collection_id == collection_id,
            VisualAssetCache.available.is_(True),
        )
        .limit(5000)
    ).all()
    rows = [row for row in rows if row.embedding]
    if not rows:
        return []
    allowed = _allowed_document_ids(db, collection_id, acl_principals or ["public"])
    if allowed is not None:
        rows = [row for row in rows if row.document_id in allowed]
    if not rows:
        return []
    try:
        query_vector = VisualEmbeddingClient().embed_text(query)
    except Exception:
        return []
    scored = []
    for row in rows:
        score = _cosine(query_vector, row.embedding or [])
        if score > 0:
            scored.append((score, row))
    if not scored:
        return []
    scored.sort(key=lambda item: item[0], reverse=True)
    max_score = scored[0][0] or 1.0
    chunks: list[RetrievedChunk] = []
    for score, row in scored[:top_k]:
        content = _visual_row_content(row)
        chunks.append(
            RetrievedChunk(
                chunk_id=f"visual:{row.asset_id}",
                document_id=row.document_id,
                title=row.filename or "视觉资产",
                title_path=["视觉资产检索索引", row.figure_no or row.page_no or row.asset_id],
                content=content,
                score=score / max_score,
                dense_score=score / max_score,
                keyword_score=None,
                rerank_score=None,
                source_uri=None,
                metadata={
                    "visual_asset": {
                        "id": row.id,
                        "asset_id": row.asset_id,
                        "document_id": row.document_id,
                        "page": row.page_no,
                        "figure_no": row.figure_no,
                        "object_key": row.object_key,
                        "retrieval_channel": "local_visual_embedding",
                    },
                    **(row.metadata_ or {}),
                },
            )
        )
    return chunks


class VisualEmbeddingClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.visual_embedding_base_url.rstrip("/")
        self.model = self.settings.visual_embedding_model
        self.headers = {
            "Authorization": f"Bearer {self.settings.visual_embedding_api_key}",
            "Content-Type": "application/json",
        }

    def embed_text(self, text: str) -> list[float]:
        return self._embed(text or "图片 图表 扫描件")

    def embed_image(self, data: bytes, content_type: str, *, context: str = "") -> list[float]:
        image = f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"
        mixed_input: list[Any] = []
        if context:
            mixed_input.append({"text": context[:1200]})
        mixed_input.append({"image": image})
        try:
            return self._embed(mixed_input)
        except Exception:
            # Some OpenAI-compatible gateways accept a bare data URL for VL embeddings.
            return self._embed(image)

    def _embed(self, input_value: Any) -> list[float]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_value,
            "encoding_format": "float",
        }
        dimensions = int(getattr(self.settings, "visual_embedding_dimensions", 0) or 0)
        if dimensions:
            payload["dimensions"] = dimensions
        timeout = float(getattr(self.settings, "visual_embedding_timeout_seconds", 120) or 120)
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{self.base_url}/embeddings", headers=self.headers, json=payload)
        response.raise_for_status()
        data = response.json()
        embedding = (((data.get("data") or [{}])[0] or {}).get("embedding")) or []
        if not embedding:
            raise RuntimeError("visual embedding response did not include an embedding")
        return [float(value) for value in embedding]


def _build_image_payload(markdown: str, artifact: dict[str, Any]) -> dict | None:
    data = artifact.get("data")
    if not isinstance(data, (bytes, bytearray)) or not data:
        return None
    source_path = str(artifact.get("name") or artifact.get("filename") or "")
    context = _nearby_context(markdown, source_path)
    figure_no = _extract_figure_no(source_path + "\n" + context)
    page_no = _extract_page_no(source_path + "\n" + context)
    caption = _extract_caption(context)
    digest = hashlib.sha256(bytes(data)).hexdigest()[:16]
    return {
        "asset_id": digest,
        "asset_type": "image",
        "filename": artifact.get("filename") or Path(source_path).name,
        "source_path": source_path,
        "content_type": artifact.get("content_type") or "image/png",
        "size": artifact.get("size") or len(data),
        "data_b64": base64.b64encode(bytes(data)).decode("ascii"),
        "page_no": page_no,
        "figure_no": figure_no,
        "caption": caption,
        "context": context,
    }


def _build_json_section(artifact: dict[str, Any]) -> str:
    text = " ".join(str(artifact.get("text") or "").split())
    if not text:
        return ""
    name = str(artifact.get("name") or artifact.get("filename") or "layout.json")
    return f"- `{name}`：{text[:1200]}"


def _nearby_context(markdown: str, source_path: str, radius: int = 3) -> str:
    lines = markdown.splitlines()
    needles = {source_path, Path(source_path).name}
    for index, line in enumerate(lines):
        if any(needle and needle in line for needle in needles):
            start = max(0, index - radius)
            end = min(len(lines), index + radius + 1)
            return " ".join(" ".join(item.strip().split()) for item in lines[start:end] if item.strip())[:1200]
    return ""


def _extract_caption(text: str) -> str | None:
    match = re.search(r"(图\s*[一二三四五六七八九十百\d]+(?:[-.]\d+)?|Figure\s*\d+(?:[-.]\d+)?)\s*[:：.、-]?\s*(.{2,160})", text or "", flags=re.IGNORECASE)
    return " ".join(match.group(0).split())[:200] if match else None


def _extract_figure_no(text: str) -> str | None:
    match = re.search(r"(图\s*[一二三四五六七八九十百\d]+(?:[-.]\d+)?|Figure\s*\d+(?:[-.]\d+)?)", text or "", flags=re.IGNORECASE)
    return re.sub(r"\s+", "", match.group(1)) if match else None


def _extract_page_no(text: str) -> str | None:
    match = re.search(r"(?:page|p|第|页)[_\-\s]*(\d{1,4})|(\d{1,4})[_\-\s]*(?:page|页)", text or "", flags=re.IGNORECASE)
    if not match:
        return None
    return next((item for item in match.groups() if item), None)


def _decode_asset_data(asset: dict) -> bytes:
    value = asset.get("data_b64")
    if not value:
        return b""
    try:
        return base64.b64decode(str(value), validate=True)
    except Exception:
        return b""


def _asset_text(asset: dict) -> str:
    return " ".join(
        str(asset.get(key) or "")
        for key in ("source_path", "figure_no", "page_no", "caption", "context")
    ).strip()


def _visual_row_content(row: VisualAssetCache) -> str:
    parts = ["# 视觉资产证据"]
    if row.figure_no:
        parts.append(f"图号：{row.figure_no}")
    if row.page_no:
        parts.append(f"页码：第 {row.page_no} 页")
    if row.caption:
        parts.append(f"图题：{row.caption}")
    if row.context:
        parts.append(f"邻近文本：{row.context}")
    if row.source_path:
        parts.append(f"MinerU 文件：{row.source_path}")
    parts.append("说明：该证据来自 MinerU 结果包中的图片/扫描件资产及其多模态向量召回，回答时仍需结合文本证据和原文页面核验。")
    return "\n".join(parts)


def _visual_embedding_available(settings: Any) -> bool:
    return bool(
        getattr(settings, "visual_embedding_enabled", False)
        and str(getattr(settings, "visual_embedding_api_key", "") or "").strip()
        and str(getattr(settings, "visual_embedding_base_url", "") or "").strip()
    )


def _looks_like_visual_query(query: str) -> bool:
    text = query or ""
    return bool(
        re.search(r"图\s*[一二三四五六七八九十百\d]?|Figure\s*\d+|图片|图像|扫描件|OCR|页面|页码|图表|截图|版面|图中|这张图", text, flags=re.IGNORECASE)
    )


def _allowed_document_ids(db: Session, collection_id: str, acl_principals: list[str]) -> set[str] | None:
    from app.models.entities import Document

    documents = db.scalars(select(Document).where(Document.collection_id == collection_id)).all()
    if not documents:
        return set()
    if {"admin", "maintainer"} & set(acl_principals):
        return None
    allowed = set()
    principals = set(acl_principals)
    for document in documents:
        acl = set(document.acl or ["public"])
        if "public" in acl or acl & principals:
            allowed.add(document.id)
    return allowed


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = math.sqrt(sum(value * value for value in left[:size])) or 1.0
    right_norm = math.sqrt(sum(value * value for value in right[:size])) or 1.0
    return max(0.0, dot / (left_norm * right_norm))


def _safe_filename(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "image")
    return text[:160] or "image"
