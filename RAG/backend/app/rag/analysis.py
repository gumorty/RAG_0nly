import re
from collections import Counter

from app.rag.normalize import estimate_tokens, normalize_text, tokenize_for_sparse
from app.rag.schemas import ChunkCandidate, ParsedDocument


SECTION_PATTERNS = {
    "progress": (
        "进展",
        "完成",
        "已完成",
        "工作内容",
        "本周",
        "实验结果",
        "结果",
        "progress",
        "done",
    ),
    "risks": (
        "风险",
        "问题",
        "困难",
        "阻塞",
        "不足",
        "失败",
        "异常",
        "risk",
        "issue",
        "blocker",
    ),
    "next_steps": (
        "计划",
        "下周",
        "下一步",
        "待办",
        "安排",
        "todo",
        "next",
    ),
    "decisions": (
        "决定",
        "决策",
        "结论",
        "方案",
        "采用",
        "确认",
        "decision",
    ),
}

REPORT_HINTS = (
    "周报",
    "会议",
    "纪要",
    "汇报",
    "本周",
    "下周",
    "工作内容",
    "完成内容",
    "风险",
    "下一步",
    "待办",
)


def build_document_analysis(parsed: ParsedDocument, chunks: list[ChunkCandidate]) -> dict:
    text = normalize_text(parsed.text)
    token_counts = [chunk.token_count for chunk in chunks]
    sparse_terms = tokenize_for_sparse(text)
    top_terms = Counter(sparse_terms).most_common(30)
    structured = extract_document_signals(text)
    warnings = _quality_warnings(text, chunks, token_counts)
    return {
        "title": parsed.title,
        "char_count": len(text),
        "estimated_tokens": estimate_tokens(text),
        "section_count": len(parsed.sections),
        "chunk_count": len(chunks),
        "chunk_token_min": min(token_counts) if token_counts else 0,
        "chunk_token_max": max(token_counts) if token_counts else 0,
        "chunk_token_avg": round(sum(token_counts) / len(token_counts), 2) if token_counts else 0,
        "top_terms": top_terms,
        "lab_signals": structured,
        "quality_warnings": warnings,
    }


def extract_document_signals(text: str) -> dict:
    normalized = normalize_text(text)
    raw_lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    lines = [_clean_signal_line(line) for line in raw_lines]
    lines = [line for line in lines if _is_signal_candidate(line)]
    owners = _extract_owner_candidates(normalized)
    key_points = [] if _looks_like_code_document(raw_lines) else _extract_key_points(lines)

    if not _looks_like_report(normalized):
        return {
            "owners": owners,
            "progress": [],
            "risks": [],
            "next_steps": [],
            "decisions": [],
            "key_points": key_points,
        }

    buckets = {name: [] for name in SECTION_PATTERNS}
    for line in lines:
        lowered = line.lower()
        for bucket, keywords in SECTION_PATTERNS.items():
            if any(keyword.lower() in lowered for keyword in keywords):
                buckets[bucket].append(line[:300])
    return {
        "owners": owners,
        "progress": _dedupe_keep_order(buckets["progress"])[:12],
        "risks": _dedupe_keep_order(buckets["risks"])[:12],
        "next_steps": _dedupe_keep_order(buckets["next_steps"])[:12],
        "decisions": _dedupe_keep_order(buckets["decisions"])[:12],
        "key_points": key_points,
    }


def _looks_like_report(text: str) -> bool:
    hint_count = sum(1 for hint in REPORT_HINTS if hint.lower() in text.lower())
    date_like = bool(re.search(r"20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}", text))
    return hint_count >= 2 or (hint_count >= 1 and date_like)


def _looks_like_code_document(lines: list[str]) -> bool:
    candidates = [line for line in lines if len(line) > 4]
    if len(candidates) < 8:
        return False
    code_lines = sum(1 for line in candidates if _looks_like_code(_clean_signal_line(line)))
    return code_lines / len(candidates) > 0.25


def _clean_signal_line(line: str) -> str:
    line = normalize_text(line)
    line = re.sub(r"(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._\-]+", r"\1<redacted>", line, flags=re.IGNORECASE)
    line = re.sub(r"(api[_-]?key\s*[:=]\s*)[A-Za-z0-9._\-]+", r"\1<redacted>", line, flags=re.IGNORECASE)
    line = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-<redacted>", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip(" -*\u3000")


def _is_signal_candidate(line: str) -> bool:
    if len(line) < 8 or len(line) > 260:
        return False
    if _looks_like_code(line):
        return False
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z]", line))


def _looks_like_code(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith(("//", "/*", "* ", "#", ".", "{", "}", "@")):
        return True
    if re.match(r"^[A-Za-z_$][\w$.-]*\s*[:=]\s*['\"{[]", stripped):
        return True
    code_markers = (
        "const ",
        "let ",
        "var ",
        "function ",
        "=>",
        "{",
        "}",
        ";",
        "window.",
        "document.",
        "class=",
        "rgba(",
        ".css",
        "throw new Error",
        "addEventListener",
        "querySelector",
        "localStorage",
        "sessionStorage",
        "GM_",
        "JSON.stringify",
        "innerHTML",
        "linear-gradient",
        "Content-Type",
        ".style.",
        ".style",
        "body:",
        "head.",
        "fetch(",
        "chrome.",
    )
    marker_hits = sum(1 for marker in code_markers if marker in line)
    symbol_ratio = len(re.findall(r"[{}();=<>/\\]", line)) / max(len(line), 1)
    return marker_hits >= 1 or symbol_ratio > 0.18


def _extract_key_points(lines: list[str]) -> list[str]:
    points = []
    for line in lines:
        if len(line) > 16 and not _looks_like_code(line):
            points.append(line[:220])
    return _dedupe_keep_order(points)[:12]


def _extract_owner_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    patterns = [
        r"(?:汇报人|负责人|作者|owner|Owner|成员)[:：]\s*([\w\u4e00-\u9fff·.-]{2,30})",
        r"([\u4e00-\u9fff]{2,4})\s*(?:负责|完成|汇报)",
    ]
    for pattern in patterns:
        candidates.extend(match.strip() for match in re.findall(pattern, text))
    return _dedupe_keep_order(candidates)[:12]


def _quality_warnings(text: str, chunks: list[ChunkCandidate], token_counts: list[int]) -> list[str]:
    warnings = []
    if len(text) < 80:
        warnings.append("document_text_too_short")
    if not chunks:
        warnings.append("no_chunks_created")
    if token_counts and max(token_counts) > 1200:
        warnings.append("chunk_too_large")
    if token_counts and min(token_counts) < 30 and len(chunks) > 1:
        warnings.append("many_tiny_chunks_possible")
    duplicate_ratio = _duplicate_line_ratio(text)
    if duplicate_ratio > 0.35:
        warnings.append("high_duplicate_line_ratio")
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text):
        warnings.append("no_meaningful_text_detected")
    return warnings


def _duplicate_line_ratio(text: str) -> float:
    lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 8]
    if not lines:
        return 0.0
    counts = Counter(lines)
    duplicate_lines = sum(count for _line, count in counts.items() if count > 1)
    return duplicate_lines / len(lines)


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result
