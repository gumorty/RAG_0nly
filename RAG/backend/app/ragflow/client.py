import json
import logging
import re
import time
from typing import Any, Generator

import httpx

from app.core.config import get_settings
from app.rag.schemas import RetrievedChunk

logger = logging.getLogger(__name__)


class RagFlowError(RuntimeError):
    pass


class RagFlowClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.ragflow_base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {self.settings.ragflow_api_key}"}

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------

    def health(self) -> dict:
        return self._request("GET", "/system/status")

    # ------------------------------------------------------------------
    # Dataset (Knowledge Base) management
    # ------------------------------------------------------------------

    def create_dataset(
        self,
        name: str,
        description: str | None = None,
        chunk_method: str | None = None,
        parser_config: dict | None = None,
    ) -> dict:
        """POST /datasets — create a RAGFlow dataset (knowledge base).

        Idempotent: if a dataset with the same name already exists, returns it.
        """
        chunk_method = chunk_method or self.settings.ragflow_default_chunk_method

        enable_kg = (parser_config or {}).get("graphrag", {}).get("use_graphrag") or (
            parser_config is None and self.settings.ragflow_kg_enabled_default
        )

        cfg = parser_config or {
            "layout_recognize": "DeepDOC",
            "chunk_token_num": self.settings.chunk_max_tokens,
            "delimiter": "\n!?;。；！？",
            "html4excel": False,
            "auto_keywords": 0,
            "auto_questions": 0,
            "parent_child": {"use_parent_child": True, "children_delimiter": "\n"},
            "raptor": {"use_raptor": False},
            "graphrag": {"use_graphrag": enable_kg},
        }
        payload = {
            "name": name[:128],
            "description": description or "Created by LLMStart RAG management platform.",
            "embedding_model": self.settings.ragflow_embedding_model,
            "chunk_method": chunk_method,
            "permission": "me",
            "parser_config": cfg,
        }
        try:
            return self._request("POST", "/datasets", json=payload)["data"]
        except RagFlowError as exc:
            err_msg = str(exc)
            if "already exists" in err_msg.lower() or "duplicate" in err_msg.lower():
                existing = self._find_dataset_by_name(name)
                if existing:
                    logger.info("Reusing existing RAGFlow dataset '%s' (id=%s)", name, existing.get("id"))
                    return existing
            raise

    def list_datasets(self, page: int = 1, page_size: int = 30) -> list[dict]:
        """GET /datasets — list all datasets."""
        resp = self._request("GET", f"/datasets?page={page}&page_size={page_size}")
        data = resp.get("data", [])
        if isinstance(data, dict):
            return data.get("datasets", data.get("items", []))
        return data if isinstance(data, list) else []

    def get_dataset(self, dataset_id: str) -> dict:
        """GET /datasets/<dataset_id> — get single dataset details."""
        return self._request("GET", f"/datasets/{dataset_id}")["data"]

    def update_dataset(self, dataset_id: str, **updates) -> dict:
        """PUT /datasets/<dataset_id> — update dataset config (chunk_method, parser_config, …)."""
        return self._request("PUT", f"/datasets/{dataset_id}", json=updates)["data"]

    def delete_dataset(self, dataset_id: str) -> None:
        """DELETE /datasets/<dataset_id> — remove a dataset."""
        self._request("DELETE", f"/datasets/{dataset_id}")

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    def upload_document(
        self, dataset_id: str, filename: str, data: bytes, content_type: str | None = None
    ) -> dict:
        files = {"file": (filename, data, content_type or "application/octet-stream")}
        try:
            response = self._request("POST", f"/datasets/{dataset_id}/documents", files=files)
            docs = response.get("data") or []
            if not docs:
                raise RagFlowError("RAGFlow uploaded no document records.")
            return docs[0]
        except RagFlowError as exc:
            err_msg = str(exc)
            # The dataset may already have a document with the same name.
            # Find and return the existing document.
            if "already exists" in err_msg.lower() or "doesn't own" in err_msg.lower():
                for doc in self.list_documents(dataset_id):
                    if doc.get("name") == filename or doc.get("filename") == filename:
                        logger.info(
                            "Reusing existing RAGFlow document '%s' (id=%s) in dataset %s",
                            filename, doc.get("id"), dataset_id,
                        )
                        return doc
            raise

    def list_documents(self, dataset_id: str) -> list[dict]:
        """GET /datasets/<dataset_id>/documents — list all docs in a dataset."""
        response = self._request("GET", f"/datasets/{dataset_id}/documents")
        data = response.get("data") or {}
        return data.get("docs") or data if isinstance(data, list) else data.get("docs", [])

    def get_document(self, dataset_id: str, document_id: str) -> dict:
        """GET /datasets/<dataset_id>/documents/<document_id> — get single document."""
        return self._request("GET", f"/datasets/{dataset_id}/documents/{document_id}")["data"]

    def update_document_parser(
        self,
        dataset_id: str,
        document_id: str,
        chunk_method: str | None = None,
        parser_config: dict | None = None,
    ) -> dict:
        """PATCH /datasets/<dataset_id>/documents/<doc_id> — override parser config per-document."""
        body: dict[str, Any] = {}
        if chunk_method:
            body["chunk_method"] = chunk_method
        if parser_config:
            body["parser_config"] = parser_config
        return self._request("PATCH", f"/datasets/{dataset_id}/documents/{document_id}", json=body)["data"]

    def delete_document(self, dataset_id: str, document_id: str) -> None:
        """DELETE /datasets/<dataset_id>/documents — delete one document by id."""
        self._request("DELETE", f"/datasets/{dataset_id}/documents", json={"ids": [document_id]})

    def parse_documents(self, dataset_id: str, document_ids: list[str]) -> None:
        """POST /datasets/<dataset_id>/chunks — trigger parsing for documents."""
        if not document_ids:
            return
        self._request("POST", f"/datasets/{dataset_id}/chunks", json={"document_ids": document_ids})

    def stop_parsing(self, dataset_id: str, document_ids: list[str]) -> None:
        """DELETE /datasets/<dataset_id>/chunks — cancel parsing for documents."""
        if not document_ids:
            return
        self._request("DELETE", f"/datasets/{dataset_id}/chunks", json={"document_ids": document_ids})

    # ------------------------------------------------------------------
    # Chunk management
    # ------------------------------------------------------------------

    def list_chunks(self, dataset_id: str, document_id: str, page: int = 1, page_size: int = 30) -> list[dict]:
        """GET /datasets/<dataset_id>/documents/<document_id>/chunks — list chunks."""
        resp = self._request(
            "GET",
            f"/datasets/{dataset_id}/documents/{document_id}/chunks?page={page}&page_size={page_size}",
        )
        return resp.get("data", {}).get("chunks", resp.get("data", []))

    def get_chunk(self, dataset_id: str, document_id: str, chunk_id: str) -> dict:
        """GET /datasets/<dataset_id>/documents/<document_id>/chunks/<chunk_id>."""
        return self._request(
            "GET", f"/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}"
        )["data"]

    def update_chunk(
        self,
        dataset_id: str,
        document_id: str,
        chunk_id: str,
        content: str | None = None,
        important_keywords: list[str] | None = None,
        available: bool | None = None,
    ) -> dict:
        """PATCH /datasets/<dataset_id>/documents/<document_id>/chunks/<chunk_id>."""
        body: dict[str, Any] = {}
        if content is not None:
            body["content"] = content
        if important_keywords is not None:
            body["important_keywords"] = important_keywords
        if available is not None:
            body["available"] = 1 if available else 0
        return self._request(
            "PATCH",
            f"/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}",
            json=body,
        )["data"]

    def delete_chunk(self, dataset_id: str, document_id: str, chunk_id: str) -> None:
        """DELETE chunk by id inside ``rm_chunk`` body."""
        self._request(
            "DELETE",
            f"/datasets/{dataset_id}/documents/{document_id}/chunks",
            json={"chunk_ids": [chunk_id]},
        )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(
        self, dataset_id: str, question: str, page_size: int = 8
    ) -> tuple[list[RetrievedChunk], dict]:
        """POST /retrieval — hybrid retrieval (dense + sparse)."""
        payload = {
            "dataset_ids": [dataset_id],
            "question": question,
            "page": 1,
            "page_size": page_size,
            "top_k": max(64, page_size * 8),
            "similarity_threshold": 0.05,
            "vector_similarity_weight": 0.3,
            "highlight": False,
        }
        response = self._request("POST", "/retrieval", json=payload)
        data = response.get("data") or {}
        chunks = []
        for item in data.get("chunks") or []:
            chunks.append(self._parse_retrieved_chunk(item))
        return _prefer_answerable_chunks(chunks), data

    def retrieve_with_reranker(
        self,
        dataset_id: str,
        question: str,
        page_size: int = 8,
        rerank_id: str | None = None,
        similarity_threshold: float = 0.05,
        vector_similarity_weight: float = 0.3,
    ) -> tuple[list[RetrievedChunk], dict]:
        """POST /retrieval — retrieval with optional server-side re-ranker."""
        payload: dict[str, Any] = {
            "dataset_ids": [dataset_id],
            "question": question,
            "page": 1,
            "page_size": page_size,
            "top_k": max(64, page_size * 8),
            "similarity_threshold": similarity_threshold,
            "vector_similarity_weight": vector_similarity_weight,
            "highlight": False,
        }
        if rerank_id:
            payload["rerank_id"] = rerank_id
        response = self._request("POST", "/retrieval", json=payload)
        data = response.get("data") or {}
        chunks = []
        for item in data.get("chunks") or []:
            chunks.append(self._parse_retrieved_chunk(item))
        return _prefer_answerable_chunks(chunks), data

    def search_datasets(
        self,
        query: str,
        dataset_ids: list[str],
        top_k: int = 8,
        similarity_threshold: float = 0.2,
        vector_similarity_weight: float = 0.3,
        use_kg: bool = False,
        rerank_id: str | None = None,
    ) -> list[dict]:
        """POST /datasets/search — retrieval test REST API (supports KG + reranker).

        Returns a list of chunk dicts as returned by RAGFlow.
        """
        payload: dict[str, Any] = {
            "dataset_ids": dataset_ids,
            "question": query,
            "top_k": top_k,
            "similarity_threshold": similarity_threshold,
            "vector_similarity_weight": vector_similarity_weight,
            "use_kg": use_kg,
            "highlight": False,
        }
        if rerank_id:
            payload["rerank_id"] = rerank_id
        resp = self._request("POST", "/datasets/search", json=payload)
        return resp.get("data", {}).get("chunks", resp.get("data", []))

    # ------------------------------------------------------------------
    # Chat / Dialog management  (Phase 2)
    # ------------------------------------------------------------------

    def create_chat(
        self,
        name: str,
        dataset_ids: list[str],
        llm_id: str | None = None,
        top_n: int = 6,
        similarity_threshold: float = 0.1,
        vector_similarity_weight: float = 0.3,
        prompt_config: dict | None = None,
        rerank_id: str | None = None,
    ) -> dict:
        """POST /chats — create a RAGFlow Dialog.

        Idempotent: if a chat with the same name already exists for these datasets,
        returns the existing one.
        """
        payload: dict[str, Any] = {
            "name": name[:128],
            "dataset_ids": dataset_ids,
            "llm_id": llm_id or self.settings.ragflow_chat_model,
            "top_n": top_n,
            "similarity_threshold": similarity_threshold,
            "vector_similarity_weight": vector_similarity_weight,
        }
        if prompt_config:
            payload["prompt_config"] = prompt_config
        if rerank_id:
            payload["rerank_id"] = rerank_id
        try:
            return self._request("POST", "/chats", json=payload)["data"]
        except RagFlowError as exc:
            err_msg = str(exc)
            if "already exists" in err_msg.lower() or "duplicate" in err_msg.lower():
                for chat in self.list_chats(page_size=100):
                    if chat.get("name") == name[:128]:
                        logger.info("Reusing existing RAGFlow chat '%s' (id=%s)", name, chat.get("id"))
                        return chat
            raise

    def list_chats(self, page: int = 1, page_size: int = 30) -> list[dict]:
        """GET /chats — list all dialogs."""
        resp = self._request("GET", f"/chats?page={page}&page_size={page_size}")
        data = resp.get("data", {})
        if isinstance(data, dict):
            return data.get("chats", data.get("items", []))
        if isinstance(data, list):
            return data
        return []

    def get_chat(self, chat_id: str) -> dict:
        """GET /chats/<chat_id> — get dialog details."""
        return self._request("GET", f"/chats/{chat_id}")["data"]

    def update_chat(self, chat_id: str, **updates) -> dict:
        """PUT /chats/<chat_id> — full replace of dialog config."""
        return self._request("PUT", f"/chats/{chat_id}", json=updates)["data"]

    def delete_chat(self, chat_id: str) -> None:
        """DELETE /chats/<chat_id> — soft-delete a dialog."""
        self._request("DELETE", f"/chats/{chat_id}")

    # ------------------------------------------------------------------
    # Session management  (Phase 2)
    # ------------------------------------------------------------------

    def create_session(self, chat_id: str, name: str = "New session") -> dict:
        """POST /chats/<chat_id>/sessions — create a conversation session."""
        return self._request("POST", f"/chats/{chat_id}/sessions", json={"name": name})["data"]

    def list_sessions(self, chat_id: str, page: int = 1, page_size: int = 30) -> list[dict]:
        """GET /chats/<chat_id>/sessions — list sessions."""
        resp = self._request("GET", f"/chats/{chat_id}/sessions?page={page}&page_size={page_size}")
        return resp.get("data", [])

    def get_session(self, chat_id: str, session_id: str) -> dict:
        """GET /chats/<chat_id>/sessions/<session_id> — get session messages + references."""
        return self._request("GET", f"/chats/{chat_id}/sessions/{session_id}")["data"]

    # ------------------------------------------------------------------
    # Chat completions  (Phase 2)
    # ------------------------------------------------------------------

    def chat_completion(
        self,
        chat_id: str,
        session_id: str,
        messages: list[dict],
        stream: bool = False,
        **gen_params: Any,
    ) -> dict | Generator[dict, None, None]:
        """POST /chat/completions — single call: retrieval + LLM + citations.

        Non-streaming returns the full response dict::
            { "answer": "...", "reference": {"chunks": [...], "doc_aggs": [...]}, "final": true }

        Streaming yields parsed SSE events as dicts with keys ``answer``, ``reference``, ``final``.
        """
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "session_id": session_id,
            "messages": messages,
            "stream": stream,
        }
        payload.update(gen_params)

        if stream:
            return self._request_stream_events("POST", "/chat/completions", json=payload)

        resp = self._request("POST", "/chat/completions", json=payload)
        return resp.get("data", {})

    # ------------------------------------------------------------------
    # Agent workflow  (Phase 3)
    # ------------------------------------------------------------------

    def list_agent_templates(self) -> list[dict]:
        """GET /agents/templates — list pre-built agent workflow templates."""
        resp = self._request("GET", "/agents/templates")
        return resp.get("data", [])

    def create_agent(self, name: str, dsl: dict, description: str = "") -> str:
        """POST /agents — create an agent from Canvas DSL.

        Returns the agent_id.
        """
        payload = {"title": name, "dsl": dsl, "description": description}
        return self._request("POST", "/agents", json=payload)["data"].get("id", "")

    def execute_agent(
        self,
        agent_id: str,
        session_id: str,
        query: str,
        stream: bool = True,
    ) -> dict | Generator[dict, None, None]:
        """POST /agents/chat/completions — execute a Canvas agent DAG."""
        payload = {
            "agent_id": agent_id,
            "session_id": session_id,
            "messages": [{"role": "user", "content": query}],
            "stream": stream,
        }
        if stream:
            return self._request_stream_events("POST", "/agents/chat/completions", json=payload)
        return self._request("POST", "/agents/chat/completions", json=payload)

    # ------------------------------------------------------------------
    # Knowledge graph
    # ------------------------------------------------------------------

    def enable_knowledge_graph(self, dataset_id: str, llm_id: str | None = None) -> dict:
        """PUT /datasets/<dataset_id> — enable GraphRAG on an existing dataset.

        Sets parser_config.graphrag.use_graphrag = True.
        """
        dataset = self.get_dataset(dataset_id)
        cfg = dataset.get("parser_config") or {}
        cfg["graphrag"] = cfg.get("graphrag") or {}
        cfg["graphrag"]["use_graphrag"] = True
        body: dict[str, Any] = {"parser_config": cfg}
        if llm_id:
            body["llm_id"] = llm_id
        return self.update_dataset(dataset_id, **body)

    def get_knowledge_graph(self, dataset_id: str) -> dict:
        """GET /datasets/<dataset_id>/graph — get the knowledge graph."""
        return self._request("GET", f"/datasets/{dataset_id}/graph")["data"]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_retrieved_chunk(self, item: dict) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=str(item.get("id") or item.get("chunk_id") or ""),
            document_id=str(item.get("document_id") or item.get("doc_id") or ""),
            title=str(item.get("document_keyword") or item.get("docnm_kwd") or "RAGFlow document"),
            content=str(item.get("content_with_weight") or item.get("content") or ""),
            title_path=[str(item.get("document_keyword") or item.get("docnm_kwd") or "RAGFlow document")],
            score=float(item.get("similarity") or 0.0),
            dense_score=_optional_float(item.get("vector_similarity")),
            keyword_score=_optional_float(item.get("term_similarity")),
            rerank_score=_optional_float(item.get("rerank_score")),
            metadata={"ragflow": item},
        )

    def _find_dataset_by_name(self, name: str) -> dict | None:
        """Search datasets by name and return the first exact match."""
        for dataset in self.list_datasets(page_size=100):
            if dataset.get("name") == name:
                return dataset
        return None

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        with httpx.Client(timeout=180) as client:
            response = client.request(method, f"{self.base_url}{path}", headers=self.headers, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in (0, None):
            raise RagFlowError(str(payload.get("message") or payload))
        return payload

    def _request_stream_events(
        self, method: str, path: str, **kwargs: Any
    ) -> Generator[dict, None, None]:
        """Make a streaming request and yield parsed SSE events as dicts.

        Yields dicts with at least the keys ``answer`` (str), ``reference`` (dict),
        and ``final`` (bool).
        """
        # httpx.Client.stream() does not accept a 'stream' kwarg — remove it
        # to avoid a TypeError when forwarding kwargs from chat_completion.
        kwargs.pop("stream", None)
        with httpx.Client(timeout=300) as client:
            with client.stream(method, f"{self.base_url}{path}", headers=self.headers, **kwargs) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    # RAGFlow SSE format: data:{...}  (NO space after colon)
                    if line.startswith("data:"):
                        data_str = line[5:]
                    elif line.startswith("data: "):
                        data_str = line[6:]
                    else:
                        continue
                    data_str = data_str.strip()
                    if not data_str:
                        continue
                    # End-of-stream marker
                    if data_str == "true" or data_str == "[DONE]":
                        return
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.warning("RAGFlow SSE parse error: %s", line[:200])
                        continue
                    # RAGFlow wraps data in {"code": 0, "data": {...}}
                    # When the stream ends, RAGFlow sends {"code": 0, "data": true}
                    inner = event.get("data", event) if "code" in event else event
                    if isinstance(inner, bool):
                        return
                    if "answer" not in inner and isinstance(inner, dict):
                        inner = event.get("data", event)
                        if isinstance(inner, bool):
                            return
                    yield inner

    # ------------------------------------------------------------------
    # Legacy helpers (kept for compat during migration)
    # ------------------------------------------------------------------

    def wait_for_documents(
        self, dataset_id: str, document_ids: list[str], timeout_seconds: int | None = None
    ) -> dict[str, dict]:
        """Poll ``list_documents`` until all given docs reach DONE/FAIL. (Legacy — prefer async)."""
        deadline = time.time() + (timeout_seconds or self.settings.ragflow_parse_timeout_seconds)
        wanted = set(document_ids)
        latest: dict[str, dict] = {}
        while time.time() < deadline:
            docs = self.list_documents(dataset_id)
            latest = {doc["id"]: doc for doc in docs if doc.get("id") in wanted}
            if wanted and wanted.issubset(latest):
                running = [doc for doc in latest.values() if str(doc.get("run", "")).upper() == "RUNNING"]
                if not running:
                    return latest
            time.sleep(5)
        return latest


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _prefer_answerable_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Keep RAGFlow ranking, but drop obvious bibliography-only snippets when alternatives exist."""
    if len(chunks) <= 1:
        return chunks
    filtered = [chunk for chunk in chunks if not _is_reference_only(chunk.content)]
    return filtered or chunks


def _is_reference_only(content: str) -> bool:
    text = re.sub(r"\s+", " ", content or "").strip()
    if not text:
        return True
    if text.startswith(("参考文献", "References", "Bibliography")):
        return True
    numbered_ref = re.match(r"^\[?\d{1,3}\]?[.、]?\s+", text)
    has_ref_marker = bool(re.search(r"\[(J|M|D|C|R|S|P|EB/OL)\]", text, re.IGNORECASE))
    has_year = bool(re.search(r"(19|20)\d{2}", text))
    return bool(numbered_ref and has_ref_marker and has_year and len(text) < 700)
