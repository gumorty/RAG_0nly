from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FigureIndexResult:
    markdown: str
    metadata: dict


def augment_markdown_with_figure_index(markdown: str, max_figures: int = 200) -> FigureIndexResult:
    """Append a lightweight figure index built from captions, image refs and nearby text.

    MinerU often keeps figure captions and page/figure catalog text even when the
    image itself is not available to the retrieval layer. This index makes those
    anchors easier to retrieve without pretending that we performed full visual
    understanding.
    """
    text = markdown or ""
    figures = _extract_figures(text, max_figures=max_figures)
    if not figures:
        return FigureIndexResult(markdown=text, metadata={"figure_index_enabled": True, "figure_count": 0})

    lines = [
        "",
        "",
        "# 图表检索索引",
        "",
        "以下索引来自文档中的图题、图片引用、图目录和邻近段落，用于图表类问题召回；若缺少图中数值，应回到原文图片/页面核验。",
    ]
    for figure in figures:
        lines.append("")
        lines.append(f"## {figure['figure_no']} {figure['title']}".strip())
        if figure.get("page"):
            lines.append(f"- 页码线索：第 {figure['page']} 页")
        if figure.get("image_ref"):
            lines.append(f"- 图片引用：{figure['image_ref']}")
        if figure.get("context"):
            lines.append(f"- 邻近文本：{figure['context']}")
        lines.append(f"- 检索关键词：{figure['figure_no']} 图表 图中 图片 页码 标题 {figure['title']}")

    metadata = {
        "figure_index_enabled": True,
        "figure_count": len(figures),
        "figure_numbers": [item["figure_no"] for item in figures[:50]],
        "figure_pages": {item["figure_no"]: item.get("page") for item in figures[:50] if item.get("page")},
    }
    return FigureIndexResult(markdown=text + "\n".join(lines), metadata=metadata)


def _extract_figures(markdown: str, max_figures: int) -> list[dict]:
    lines = markdown.splitlines()
    figures: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for index, line in enumerate(lines):
        stripped = " ".join(line.strip().split())
        if not stripped:
            continue

        image = _parse_image_ref(stripped)
        caption = _parse_figure_caption(stripped)
        catalog = _parse_figure_catalog_line(stripped)
        candidate = caption or catalog

        if image and not candidate:
            nearby = _nearby_text(lines, index)
            candidate = _parse_figure_caption(nearby) or {"figure_no": "图", "title": nearby[:120], "page": None}
            candidate["image_ref"] = image
        elif image and candidate:
            candidate["image_ref"] = image

        if not candidate:
            continue

        figure_no = _normalize_figure_no(candidate.get("figure_no") or "图")
        title = _clean_title(candidate.get("title") or stripped)
        page = candidate.get("page") or _extract_page_hint(stripped)
        context = _nearby_text(lines, index)
        key = (figure_no, title)
        if key in seen:
            continue
        seen.add(key)
        figures.append(
            {
                "figure_no": figure_no,
                "title": title,
                "page": page,
                "image_ref": candidate.get("image_ref"),
                "context": context,
            }
        )
        if len(figures) >= max_figures:
            break
    return figures


def _parse_image_ref(line: str) -> str | None:
    match = re.search(r"!\[[^\]]*\]\(([^)]+)\)", line)
    return match.group(1).strip() if match else None


def _parse_figure_caption(line: str) -> dict | None:
    match = re.search(
        r"(图\s*[一二三四五六七八九十百\d]+(?:[-.]\d+)?|Figure\s*\d+(?:[-.]\d+)?)\s*[:：.、-]?\s*(.{2,160})",
        line,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    title = _strip_page_tail(match.group(2))
    return {"figure_no": match.group(1), "title": title, "page": _extract_page_hint(line)}


def _parse_figure_catalog_line(line: str) -> dict | None:
    if not re.search(r"图\s*[一二三四五六七八九十百\d]+|Figure\s*\d+", line, flags=re.IGNORECASE):
        return None
    if not re.search(r"\.{2,}\s*\d+$|\s+\d{1,4}$", line):
        return None
    match = re.search(r"(图\s*[一二三四五六七八九十百\d]+(?:[-.]\d+)?|Figure\s*\d+(?:[-.]\d+)?)\s*[:：.、-]?\s*(.+?)\s*(?:\.{2,}|\s+)(\d{1,4})$", line, flags=re.IGNORECASE)
    if not match:
        return None
    return {"figure_no": match.group(1), "title": match.group(2), "page": match.group(3)}


def _nearby_text(lines: list[str], index: int, radius: int = 2, limit: int = 420) -> str:
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    text = " ".join(" ".join(line.strip().split()) for line in lines[start:end] if line.strip())
    return text[:limit]


def _normalize_figure_no(value: str) -> str:
    text = re.sub(r"\s+", "", value or "图")
    text = re.sub(r"^Figure", "Figure ", text, flags=re.IGNORECASE)
    return text


def _clean_title(value: str) -> str:
    text = _strip_page_tail(" ".join((value or "").split()))
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text).strip(" -:：.。")
    return text[:180] or "未命名图表"


def _strip_page_tail(value: str) -> str:
    return re.sub(r"(?:\.{2,}|\s+)\d{1,4}$", "", value or "").strip()


def _extract_page_hint(value: str) -> str | None:
    match = re.search(r"(?:第\s*)?(\d{1,4})\s*页", value or "")
    if match:
        return match.group(1)
    match = re.search(r"\.{2,}\s*(\d{1,4})$", value or "")
    return match.group(1) if match else None
