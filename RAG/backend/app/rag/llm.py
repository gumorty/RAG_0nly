from typing import Any

from app.core.config import get_settings
from app.rag.schemas import ChatTurn, RetrievedChunk


class LLMClient:
    def __init__(
        self,
        provider: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.settings = get_settings()
        self.provider = provider or self.settings.llm_provider
        self.base_url = base_url or self.settings.llm_base_url
        self.api_key = api_key or self.settings.llm_api_key
        self.model = model or self.settings.llm_model
        self.temperature = 0.2 if temperature is None else temperature
        self.max_tokens = max_tokens or 1600
        self.client = None
        if self.provider != "mock":
            from openai import OpenAI

            self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    def rewrite_query(self, question: str) -> str:
        if self.provider == "mock":
            return question
        prompt = (
            "Rewrite the user question into a short enterprise knowledge-base "
            "retrieval query. Keep entities, dates, project names, metrics, and "
            "technical terms. Return only the rewritten query.\n\n"
            f"User question: {question}"
        )
        return self._chat(prompt, temperature=0.0, max_tokens=256).strip() or question

    def answer(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        min_score: float,
        history: list[ChatTurn] | None = None,
    ) -> str:
        if not chunks or max(chunk.score for chunk in chunks) < min_score:
            return "\u5f53\u524d\u77e5\u8bc6\u5e93\u6ca1\u6709\u8db3\u591f\u53ef\u9760\u7684\u8bc1\u636e\u56de\u7b54\u8fd9\u4e2a\u95ee\u9898\u3002"
        if self.provider == "mock":
            lines = [
                "\u57fa\u4e8e\u5f53\u524d\u77e5\u8bc6\u5e93\u8bc1\u636e\u7684\u56de\u7b54\uff1a",
                f"\u95ee\u9898\uff1a{question}",
                "\u8bc1\u636e\uff1a",
            ]
            for index, chunk in enumerate(chunks[:5], start=1):
                preview = chunk.content.replace("\n", " ")[:220]
                lines.append(f"[{index}] {chunk.title}: {preview}")
            return "\n".join(lines)

        context = "\n\n".join(
            f"[{index}] Source: {chunk.title} / {' > '.join(chunk.title_path)}\n{chunk.content}"
            for index, chunk in enumerate(chunks, start=1)
        )
        recent_history = self._format_history(history or [])
        prompt = f"""You are an enterprise RAG knowledge-base assistant.
Answer strictly from the retrieved evidence.

Rules:
1. Do not invent facts that are not present in the evidence.
2. Add citation markers after key conclusions, for example [1].
3. If evidence is insufficient, state exactly what is missing.
4. Keep the answer structured, actionable, and concise.
5. Answer in the same language as the user's question.
6. Use recent conversation only to resolve follow-up references such as "it",
   "these issues", or "the previous document". Do not treat conversation history
   as factual evidence unless the retrieved evidence also supports it.
7. Do not repeat the same conclusion, sentence, bullet, or citation group.

Recent conversation:
{recent_history or "None"}

User question:
{question}

Evidence:
{context}
"""
        return self._chat(prompt, temperature=self.temperature, max_tokens=self.max_tokens)

    def _chat(self, prompt: str, temperature: float, max_tokens: int) -> str:
        if self.client is None:
            raise RuntimeError("LLM client is not configured")
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _extract_content(response)

    def _format_history(self, history: list[ChatTurn], limit: int = 8) -> str:
        if not history:
            return ""
        lines = []
        for turn in history[-limit:]:
            content = " ".join(turn.content.split())
            if len(content) > 900:
                content = content[:900] + "..."
            label = "User" if turn.role == "user" else "Assistant"
            lines.append(f"{label}: {content}")
        return "\n".join(lines)


def _extract_content(response: Any) -> str:
    if isinstance(response, str):
        return _reject_html(response)
    if isinstance(response, dict):
        choices = response.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            return _reject_html(str(message.get("content") or choices[0].get("text") or ""))
        return _reject_html(str(response.get("content") or response.get("text") or ""))
    choices = getattr(response, "choices", None)
    if choices:
        first = choices[0]
        message = getattr(first, "message", None)
        if message is not None:
            return _reject_html(str(getattr(message, "content", "") or ""))
        return _reject_html(str(getattr(first, "text", "") or ""))
    return _reject_html(str(response or ""))


def _reject_html(content: str) -> str:
    stripped = content.strip()
    if stripped.lower().startswith("<!doctype html") or stripped.lower().startswith("<html"):
        raise RuntimeError(
            "Model route returned an HTML page. Check that Base URL points to an "
            "OpenAI-compatible /v1 API endpoint, not a console or web page."
        )
    return content
