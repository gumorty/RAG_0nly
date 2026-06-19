from app.rag.normalize import estimate_tokens, normalize_text
from app.rag.schemas import ChunkCandidate, ParsedDocument


class HierarchicalChunker:
    def __init__(self, max_tokens: int, overlap_tokens: int):
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, parsed: ParsedDocument) -> list[ChunkCandidate]:
        chunks: list[ChunkCandidate] = []
        sections = parsed.sections or [{"title_path": ["正文"], "text": parsed.text}]
        for section in sections:
            title_path = section.get("title_path") or ["正文"]
            text = normalize_text(section.get("text", ""))
            for part in self._split_text(text):
                chunks.append(
                    ChunkCandidate(
                        content=part,
                        normalized_content=normalize_text(part),
                        title_path=title_path,
                        token_count=estimate_tokens(part),
                        metadata={"section": " / ".join(title_path)},
                    )
                )
        return chunks

    def _split_text(self, text: str) -> list[str]:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: list[str] = []
        current: list[str] = []
        current_tokens = 0
        for paragraph in paragraphs:
            paragraph_tokens = estimate_tokens(paragraph)
            if paragraph_tokens > self.max_tokens:
                if current:
                    chunks.append("\n\n".join(current))
                    current, current_tokens = [], 0
                chunks.extend(self._split_long_paragraph(paragraph))
                continue
            if current and current_tokens + paragraph_tokens > self.max_tokens:
                chunks.append("\n\n".join(current))
                current = self._build_overlap(current)
                current_tokens = estimate_tokens("\n\n".join(current))
            current.append(paragraph)
            current_tokens += paragraph_tokens
        if current:
            chunks.append("\n\n".join(current))
        return chunks

    def _split_long_paragraph(self, paragraph: str) -> list[str]:
        sentences = []
        buffer = ""
        for char in paragraph:
            buffer += char
            if char in "。！？.!?\n":
                sentences.append(buffer.strip())
                buffer = ""
        if buffer.strip():
            sentences.append(buffer.strip())

        chunks: list[str] = []
        current: list[str] = []
        for sentence in sentences:
            if estimate_tokens("".join(current) + sentence) > self.max_tokens and current:
                chunks.append("".join(current))
                current = self._build_overlap(current)
            current.append(sentence)
        if current:
            chunks.append("".join(current))
        return chunks

    def _build_overlap(self, parts: list[str]) -> list[str]:
        if self.overlap_tokens <= 0:
            return []
        overlap: list[str] = []
        total = 0
        for part in reversed(parts):
            total += estimate_tokens(part)
            overlap.insert(0, part)
            if total >= self.overlap_tokens:
                break
        return overlap
