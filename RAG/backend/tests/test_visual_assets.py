import io
import json
import zipfile
from types import SimpleNamespace

from app.services import mineru, visual_assets


def test_mineru_extracts_images_and_layout_json_from_zip():
    settings = SimpleNamespace(
        visual_asset_max_images=5,
        visual_asset_max_image_mb=2,
        visual_asset_max_json_chars=2000,
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("full.md", "# Report\n\n![图6](images/page_35_fig_6.png)")
        archive.writestr("images/page_35_fig_6.png", b"\x89PNG\r\n\x1a\nfake")
        archive.writestr("layout/middle.json", json.dumps({"figures": [{"caption": "图6 AI 应用产业链分布"}]}, ensure_ascii=False))
    buffer.seek(0)

    with zipfile.ZipFile(buffer) as archive:
        artifacts = mineru._extract_indexable_artifacts(archive, archive.namelist(), settings)

    assert [item["kind"] for item in artifacts] == ["image", "json"]
    assert artifacts[0]["name"] == "images/page_35_fig_6.png"
    assert "图6" in artifacts[1]["text"]


def test_visual_assets_append_searchable_index_and_payloads(monkeypatch):
    monkeypatch.setattr(
        visual_assets,
        "get_settings",
        lambda: SimpleNamespace(visual_asset_index_enabled=True),
    )
    markdown = "# Report\n\n图 6 AI 应用产业链分布\n\n![图6](images/page_35_fig_6.png)"
    augmented, metadata, payloads = visual_assets.augment_markdown_with_visual_assets(
        markdown,
        [
            {
                "kind": "image",
                "name": "images/page_35_fig_6.png",
                "filename": "page_35_fig_6.png",
                "content_type": "image/png",
                "size": 12,
                "data": b"\x89PNG\r\n\x1a\nfake",
            },
            {
                "kind": "json",
                "name": "layout/middle.json",
                "text": "图6 显示研发设计、生产制造、营销服务等产业链分布。",
            },
        ],
    )

    assert metadata["visual_asset_count"] == 1
    assert payloads[0]["figure_no"] == "图6"
    assert payloads[0]["page_no"] == "35"
    assert "视觉资产检索索引" in augmented
    assert "MinerU 版面与结构化文本" in augmented


def test_visual_embedding_client_uses_mixed_text_image_payload(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(visual_assets.httpx, "Client", FakeClient)
    monkeypatch.setattr(
        visual_assets,
        "get_settings",
        lambda: SimpleNamespace(
            visual_embedding_base_url="https://api.siliconflow.cn/v1",
            visual_embedding_api_key="test-key",
            visual_embedding_model="Qwen/Qwen3-VL-Embedding-8B",
            visual_embedding_dimensions=1024,
            visual_embedding_timeout_seconds=30,
        ),
    )

    vector = visual_assets.VisualEmbeddingClient().embed_image(b"fake-image", "image/png", context="图6 AI 应用产业链分布")

    assert vector == [0.1, 0.2, 0.3]
    assert captured["url"].endswith("/embeddings")
    assert captured["json"]["model"] == "Qwen/Qwen3-VL-Embedding-8B"
    assert captured["json"]["dimensions"] == 1024
    assert captured["json"]["input"][0]["text"].startswith("图6")
    assert captured["json"]["input"][1]["image"].startswith("data:image/png;base64,")
