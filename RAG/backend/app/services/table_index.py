from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any


@dataclass(frozen=True)
class TableIndexResult:
    markdown: str
    metadata: dict[str, Any]


TABLE_INDEX_HEADING = "表格行级检索索引"


def augment_markdown_with_table_index(markdown: str, max_rows: int = 5000) -> TableIndexResult:
    """Append retrieval-friendly row facts extracted from Markdown/HTML tables."""
    source = markdown or ""
    if TABLE_INDEX_HEADING in source:
        return TableIndexResult(
            markdown=source,
            metadata={"table_index_enabled": True, "table_count": 0, "table_row_count": 0, "skipped": "already_indexed"},
        )

    tables = _extract_tables(source)
    row_lines: list[str] = []
    total_rows = 0
    truncated = False

    for table_index, rows in enumerate(tables, start=1):
        if len(rows) < 2:
            continue
        header = _build_header(rows)
        if not header:
            continue
        body_start = _body_start_index(rows)
        context_values: list[str] = [""] * len(header)

        for row_index, raw_row in enumerate(rows[body_start:], start=1):
            if total_rows >= max_rows:
                truncated = True
                break
            row = _fit_row(raw_row, len(header))
            if _is_separator_row(row):
                continue
            repaired = _forward_fill_context_cells(row, header, context_values)
            pairs = _row_to_pairs(header, repaired)
            if len(pairs) < 2:
                continue
            row_lines.append(f"表格{table_index} 第{row_index}行：" + "；".join(pairs) + "。")
            total_rows += 1
        if truncated:
            break

    metadata = {
        "table_index_enabled": True,
        "table_count": len(tables),
        "table_row_count": total_rows,
        "table_index_truncated": truncated,
    }
    if not row_lines:
        return TableIndexResult(markdown=source, metadata=metadata)

    suffix = (
        "\n\n---\n\n"
        f"# {TABLE_INDEX_HEADING}\n\n"
        "以下内容是系统根据文档表格生成的行级事实索引，用于精确检索。"
        "回答时必须结合原文与行级索引；如果某行没有明确给出字段，不得从相邻行推断。\n\n"
        + "\n".join(row_lines)
        + "\n"
    )
    return TableIndexResult(markdown=source.rstrip() + suffix, metadata=metadata)


def _extract_tables(markdown: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    tables.extend(_extract_markdown_pipe_tables(markdown))
    tables.extend(_extract_html_tables(markdown))
    return tables


def _extract_markdown_pipe_tables(markdown: str) -> list[list[list[str]]]:
    lines = markdown.splitlines()
    tables: list[list[list[str]]] = []
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        if _looks_like_pipe_table_line(stripped):
            current.append(stripped)
        else:
            _flush_pipe_table(current, tables)
            current = []
    _flush_pipe_table(current, tables)
    return tables


def _looks_like_pipe_table_line(line: str) -> bool:
    return bool(line) and "|" in line and line.count("|") >= 2


def _flush_pipe_table(lines: list[str], tables: list[list[list[str]]]) -> None:
    if len(lines) < 2:
        return
    parsed: list[list[str]] = []
    for line in lines:
        row = [cell.strip() for cell in _split_markdown_row(line)]
        if not row or _is_separator_row(row):
            continue
        parsed.append(row)
    if len(parsed) >= 2:
        tables.append(parsed)


def _split_markdown_row(line: str) -> list[str]:
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    text = line.strip().strip("|")
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "|":
            cells.append("".join(current))
            current = []
            continue
        current.append(char)
    cells.append("".join(current))
    return cells


def _extract_html_tables(markdown: str) -> list[list[list[str]]]:
    if "<table" not in markdown.lower() or "</table" not in markdown.lower():
        return []
    parser = _HTMLTableParser()
    try:
        parser.feed(markdown)
        parser.close()
    except Exception:
        return []
    return [table for table in parser.tables if len(table) >= 2]


@dataclass
class _Cell:
    text: str
    rowspan: int = 1
    colspan: int = 1


class _HTMLTableParser(HTMLParser):
    """HTML table parser that expands simple rowspan/colspan grids."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_cell = False
        self._current_rows: list[list[_Cell]] = []
        self._current_row: list[_Cell] = []
        self._cell_parts: list[str] = []
        self._cell_rowspan = 1
        self._cell_colspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._in_table = True
            self._current_rows = []
        elif self._in_table and tag == "tr":
            self._current_row = []
        elif self._in_table and tag in {"td", "th"}:
            attr = {key.lower(): value for key, value in attrs if key}
            self._in_cell = True
            self._cell_parts = []
            self._cell_rowspan = _positive_int(attr.get("rowspan"), 1)
            self._cell_colspan = _positive_int(attr.get("colspan"), 1)
        elif self._in_cell and tag in {"br", "p", "div"}:
            self._cell_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            self._current_row.append(
                _Cell(
                    text=_normalize_cell("".join(self._cell_parts)),
                    rowspan=self._cell_rowspan,
                    colspan=self._cell_colspan,
                )
            )
            self._cell_parts = []
            self._in_cell = False
        elif tag == "tr" and self._in_table:
            if any(cell.text.strip() for cell in self._current_row):
                self._current_rows.append(self._current_row)
            self._current_row = []
        elif tag == "table" and self._in_table:
            expanded = _expand_spans(self._current_rows)
            if len(expanded) >= 2:
                self.tables.append(expanded)
            self._current_rows = []
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, 100))


def _expand_spans(rows: list[list[_Cell]]) -> list[list[str]]:
    grid: list[list[str]] = []
    pending: dict[tuple[int, int], str] = {}

    for row_index, row in enumerate(rows):
        out: list[str] = []
        col_index = 0
        for cell in row:
            while (row_index, col_index) in pending:
                out.append(pending.pop((row_index, col_index)))
                col_index += 1

            for offset in range(cell.colspan):
                value = cell.text if offset == 0 else cell.text
                out.append(value)
                if cell.rowspan > 1:
                    for delta in range(1, cell.rowspan):
                        pending[(row_index + delta, col_index + offset)] = value
            col_index += cell.colspan

        while (row_index, col_index) in pending:
            out.append(pending.pop((row_index, col_index)))
            col_index += 1
        grid.append(out)

    if pending:
        max_row = max(row for row, _ in pending)
        for row_index in range(len(grid), max_row + 1):
            out: list[str] = []
            col_index = 0
            while (row_index, col_index) in pending:
                out.append(pending.pop((row_index, col_index)))
                col_index += 1
            if out:
                grid.append(out)

    width = max((len(row) for row in grid), default=0)
    return [row + [""] * (width - len(row)) for row in grid if any(cell.strip() for cell in row)]


def _build_header(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    first = [_normalize_cell(value) for value in rows[0]]
    if len(rows) >= 2 and (_looks_like_header_continuation(rows[1]) or _has_colspan_like_parent(first)):
        second = _fit_row(rows[1], len(first))
        return [_combine_header_cell(a, b, index) for index, (a, b) in enumerate(zip(first, second, strict=False))]
    return [_fallback_header(value, index) for index, value in enumerate(first)]


def _body_start_index(rows: list[list[str]]) -> int:
    first = [_normalize_cell(value) for value in rows[0]] if rows else []
    if len(rows) >= 2 and (_looks_like_header_continuation(rows[1]) or _has_colspan_like_parent(first)):
        return 2
    return 1


def _looks_like_header_continuation(row: list[str]) -> bool:
    text = " ".join(row)
    header_terms = ("部级", "司局", "其他", "人员", "旺季", "期间", "上浮", "标准", "数量", "金额")
    digit_cells = sum(1 for cell in row if re.search(r"\d", cell or ""))
    return any(term in text for term in header_terms) and digit_cells <= max(1, len(row) // 3)


def _has_colspan_like_parent(row: list[str]) -> bool:
    normalized = [_normalize_cell(value) for value in row]
    for left, right in zip(normalized, normalized[1:], strict=False):
        if left and left == right:
            return True
    return False


def _combine_header_cell(parent: str, child: str, index: int) -> str:
    parent = _normalize_cell(parent)
    child = _normalize_cell(child)
    if parent and child and parent != child:
        return f"{parent}_{child}"
    return _fallback_header(parent or child, index)


def _fallback_header(value: str, index: int) -> str:
    value = _normalize_cell(value)
    return value or f"列{index + 1}"


def _fit_row(row: list[str], size: int) -> list[str]:
    normalized = [_normalize_cell(value) for value in row]
    if len(normalized) < size:
        normalized.extend([""] * (size - len(normalized)))
    return normalized[:size]


def _forward_fill_context_cells(row: list[str], header: list[str], context_values: list[str]) -> list[str]:
    repaired: list[str] = []
    for index, value in enumerate(row):
        if not value and _is_context_column(header[index], index):
            value = context_values[index]
        if value and _is_context_column(header[index], index):
            context_values[index] = value
        repaired.append(value)
    return repaired


def _is_context_column(header_name: str, index: int) -> bool:
    if index <= 2:
        return True
    return any(term in header_name for term in ("省", "地区", "城市", "类别", "项目", "名称", "序号", "章节", "标题"))


def _row_to_pairs(header: list[str], row: list[str]) -> list[str]:
    pairs: list[str] = []
    for key, value in zip(header, row, strict=False):
        key = _normalize_cell(key)
        value = _normalize_cell(value)
        if key and value:
            pairs.append(f"{key}={value}")
    return pairs


def _is_separator_row(row: list[str]) -> bool:
    if not row:
        return True
    return all(re.fullmatch(r":?-{2,}:?", (cell or "").strip()) for cell in row if (cell or "").strip())


def _normalize_cell(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = text.replace("\u3000", " ")
    return " ".join(text.split())
