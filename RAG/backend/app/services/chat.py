import logging
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Generator

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import get_settings
from app.models.entities import Answer, Collection, Document, ModelConfig, RagStrategyPreset, RetrievalTrace
from app.rag.llm import LLMClient
from app.rag.retrieval import RetrievalService
from app.rag.schemas import ChatRequest, ChatResponse, ChatTurn, RetrievalStrategy
from app.ragflow.client import RagFlowClient, RagFlowError

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        active_model = self._active_model()
        self.llm = self._llm_client(active_model)
        self.model_name = active_model.model_name if active_model else self.settings.llm_model
        self.retrieval = RetrievalService(db)

    def ask(self, request: ChatRequest) -> ChatResponse:
        if self.settings.ragflow_enabled:
            return self._ask_with_ragflow(request)

        strategy = request.strategy or self._default_strategy()
        rewritten = self._contextualize_question(request.question, request.history) if strategy.use_query_rewrite else request.question
        chunks = self.retrieval.retrieve(
            request.collection_id,
            rewritten,
            strategy,
            acl_principals=request.user_acl_principals or ([request.user_id, "public"] if request.user_id else ["public"]),
        )
        evidence_score = max([chunk.score for chunk in chunks], default=0.0)
        answer_text = _clean_answer_text(self.llm.answer(request.question, chunks, strategy.min_evidence_score, history=request.history))
        citations = [_citation_from_retrieved_chunk(chunk) for chunk in chunks]
        trace = RetrievalTrace(
            collection_id=request.collection_id,
            query=request.question,
            rewritten_query=rewritten,
            strategy=strategy.model_dump(),
            candidates=[citation for citation in citations],
            selected_context=citations,
        )
        self.db.add(trace)
        self.db.flush()
        answer = Answer(
            trace_id=trace.id,
            collection_id=request.collection_id,
            user_id=request.user_id,
            session_id=request.session_id,
            question=request.question,
            answer=answer_text,
            citations=citations,
            model=self.model_name,
            evidence_score=evidence_score,
        )
        self.db.add(answer)
        self.db.commit()
        return ChatResponse(
            answer_id=answer.id,
            trace_id=trace.id,
            session_id=answer.session_id,
            answer=answer.answer,
            citations=citations,
            evidence_score=evidence_score,
            model=answer.model,
        )

    # ------------------------------------------------------------------
    # RAGFlow dispatch: use retrieval-only as primary path
    # ------------------------------------------------------------------

    def _ask_with_ragflow(self, request: ChatRequest) -> ChatResponse:
        collection = self.db.scalar(select(Collection).where(Collection.id == request.collection_id))
        if not collection:
            raise RuntimeError("Collection not found")

        dataset_id = (collection.metadata_ or {}).get("ragflow_dataset_id")
        if not dataset_id:
            raise RuntimeError("This collection is not linked to a RAGFlow dataset yet.")

        # Priority 1: retrieval-only — RAGFlow retrieves, our LLM answers.
        # This gives the best answer quality because we control the prompt
        # and evidence formatting.  Chat Completions delegates to RAGFlow's
        # internal LLM which may have a suboptimal system prompt.
        try:
            return self._ask_with_ragflow_retrieval_only(request, collection, str(dataset_id))
        except (RagFlowError, ConnectionError, TimeoutError) as exc:
            logger.warning("RAGFlow retrieval-only failed, trying chat completions: %s", exc)

        # Priority 2: Chat Completions (RAGFlow retrieves + its LLM answers)
        chat_id = (collection.metadata_ or {}).get("ragflow_chat_id")
        if self.settings.ragflow_enable_chat_completions and self._can_use_unfiltered_ragflow_chat(request, collection):
            if not chat_id:
                try:
                    chat_id = self._ensure_ragflow_chat(collection)
                except RagFlowError as exc:
                    logger.warning("Could not create RAGFlow chat for collection %s: %s", collection.id, exc)
            if chat_id:
                try:
                    return self._ask_with_ragflow_chat(request, collection, str(chat_id))
                except (RagFlowError, ConnectionError, TimeoutError) as exc:
                    logger.warning("RAGFlow chat completions also failed: %s", exc)
        elif self.settings.ragflow_enable_chat_completions:
            logger.warning("Skipping RAGFlow chat completion fallback because ACL filtering cannot be enforced before generation")

        raise RuntimeError("All RAGFlow retrieval paths failed")

    # ------------------------------------------------------------------
    # Preferred: RAGFlow Chat Completions (retrieval + LLM + citations in one call)
    # ------------------------------------------------------------------

    def _ask_with_ragflow_chat(
        self,
        request: ChatRequest,
        collection: Collection,
        chat_id: str,
    ) -> ChatResponse:
        from app.services.ragflow_session import RagflowSessionManager

        mgr = RagflowSessionManager(self.db)
        _, session_id = mgr.get_or_create_session(
            collection_id=request.collection_id,
            user_id=request.user_id,
        )

        # Only send the current question — RAGFlow maintains session history server-side
        messages = [{"role": "user", "content": request.question}]

        client = RagFlowClient()
        response = client.chat_completion(
            chat_id=chat_id,
            session_id=session_id,
            messages=messages,
            stream=False,
        )

        answer_text = _clean_answer_text(str(response.get("answer") or ""))
        reference = response.get("reference", {}) or {}
        ragflow_chunks = reference.get("chunks", []) or []

        citations = _map_ragflow_citations(ragflow_chunks)
        evidence_score = _compute_ragflow_evidence_score(ragflow_chunks)

        trace = RetrievalTrace(
            collection_id=request.collection_id,
            query=request.question,
            rewritten_query=request.question,
            strategy={
                "engine": "ragflow_chat_completions",
                "chat_id": chat_id,
                "session_id": session_id,
                "ragflow_dataset_id": collection.metadata_.get("ragflow_dataset_id"),
            },
            candidates=citations,
            selected_context=citations,
        )
        self.db.add(trace)
        self.db.flush()

        answer = Answer(
            trace_id=trace.id,
            collection_id=request.collection_id,
            user_id=request.user_id,
            session_id=request.session_id,
            question=request.question,
            answer=answer_text,
            citations=citations,
            model=f"{self.settings.ragflow_chat_model} via RAGFlow chat",
            evidence_score=evidence_score,
        )
        self.db.add(answer)
        self.db.commit()

        return ChatResponse(
            answer_id=answer.id,
            trace_id=trace.id,
            session_id=answer.session_id,
            answer=answer.answer,
            citations=citations,
            evidence_score=evidence_score,
            model=answer.model,
        )

    # ------------------------------------------------------------------
    # Agent Workflow path (multi-step DAG execution)
    # ------------------------------------------------------------------

    def _ask_with_ragflow_agent(
        self,
        request: ChatRequest,
        collection: Collection,
        agent_id: str,
    ) -> ChatResponse:
        """Use a RAGFlow Agent Canvas DAG for complex multi-step queries.

        The agent DAG can include LLM calls, retrievals, conditional
        branching (Categorize/Switch), iteration loops, etc.

        Execution chain::

            POST /agents/chat/completions
              → Canvas DAG execution (LLM → Retrieve → LLM → ...)
              → Returns {answer, reference: {chunks, doc_aggs}}
        """
        from app.ragflow.agent import RagflowAgentClient

        agent_client = RagflowAgentClient()

        # Get or create an agent session
        session = agent_client.create_agent_session(agent_id)
        session_id = session.get("id", "")
        if not session_id:
            raise RagFlowError(f"Failed to create agent session for agent {agent_id}")

        messages: list[dict] = []
        for turn in request.history:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": request.question})

        response = agent_client.execute_agent(
            agent_id=agent_id,
            session_id=session_id,
            messages=messages,
            stream=False,
        )

        answer_text = _clean_answer_text(str(response.get("answer") or ""))
        reference = response.get("reference", {}) or {}
        ragflow_chunks = reference.get("chunks", []) or []

        citations = _map_ragflow_citations(ragflow_chunks)
        evidence_score = _compute_ragflow_evidence_score(ragflow_chunks)

        trace = RetrievalTrace(
            collection_id=request.collection_id,
            query=request.question,
            strategy={
                "engine": "ragflow_agent",
                "agent_id": agent_id,
                "session_id": session_id,
            },
            candidates=citations,
            selected_context=citations,
        )
        self.db.add(trace)
        self.db.flush()

        answer = Answer(
            trace_id=trace.id,
            collection_id=request.collection_id,
            user_id=request.user_id,
            session_id=request.session_id,
            question=request.question,
            answer=answer_text,
            citations=citations,
            model=f"{self.settings.ragflow_chat_model} via RAGFlow Agent",
            evidence_score=evidence_score,
        )
        self.db.add(answer)
        self.db.commit()

        return ChatResponse(
            answer_id=answer.id,
            trace_id=trace.id,
            session_id=answer.session_id,
            answer=answer.answer,
            citations=citations,
            evidence_score=evidence_score,
            model=answer.model,
        )

    def ask_stream(self, request: ChatRequest) -> Generator[dict[str, Any], None, None]:
        """Streaming version — yields SSE-compatible dicts, final event includes full answer + references."""
        collection = self.db.scalar(select(Collection).where(Collection.id == request.collection_id))
        if not collection:
            yield {"error": "Collection not found", "final": True}
            return

        dataset_id = (collection.metadata_ or {}).get("ragflow_dataset_id")
        if not dataset_id:
            yield {"error": "This collection is not linked to a RAGFlow dataset yet.", "final": True}
            return

        # Use retrieval-only — RAGFlow retrieves, we stream the answer.
        # This gives better answer quality than Chat Completions because
        # we control the prompt and evidence formatting.
        try:
            response = self._ask_with_ragflow_retrieval_only(request, collection, str(dataset_id))
            yield {
                "answer": response.answer,
                "reference": {"chunks": response.citations or []},
                "final": True,
            }
        except Exception as exc:
            logger.error("RAGFlow streaming retrieval-only error: %s", exc)

            # Fallback to Chat Completions streaming
            chat_id = (collection.metadata_ or {}).get("ragflow_chat_id")
            if self.settings.ragflow_enable_chat_completions:
                if not chat_id:
                    try:
                        chat_id = self._ensure_ragflow_chat(collection)
                    except RagFlowError as chat_exc:
                        logger.warning("Could not create RAGFlow chat for streaming: %s", chat_exc)
                if chat_id:
                    yield from self._ask_stream_via_ragflow_chat(request, collection, str(chat_id), str(dataset_id))
                    return

            yield {"error": str(exc), "final": True}

    def _ask_stream_via_ragflow_chat(
        self,
        request: ChatRequest,
        collection: Collection,
        chat_id: str,
        dataset_id: str,
    ) -> Generator[dict[str, Any], None, None]:
        from app.services.ragflow_session import RagflowSessionManager

        mgr = RagflowSessionManager(self.db)
        _, session_id = mgr.get_or_create_session(
            collection_id=request.collection_id,
            user_id=request.user_id,
        )

        # Only send the current question — RAGFlow maintains session history server-side
        messages = [{"role": "user", "content": request.question}]

        client = RagFlowClient()
        final_answer = ""
        final_reference = {}

        try:
            for event in client.chat_completion(
                chat_id=chat_id,
                session_id=session_id,
                messages=messages,
                stream=True,
            ):
                if not event:
                    continue
                chunk = event.get("answer", "")
                if chunk:
                    final_answer += chunk
                ref = event.get("reference")
                if ref:
                    final_reference = ref
                if not event.get("final"):
                    yield {
                        "answer": chunk,
                        "reference": ref or {},
                        "final": False,
                    }
        except Exception as exc:
            logger.error("RAGFlow streaming error: %s", exc)
            yield {"answer": "", "reference": {}, "final": True, "error": str(exc)}
            return

        # If Chat Completions returned nothing and we have documents in the
        # collection, fall back to the retrieval-only path (bypasses the
        # chat completions dispatch to avoid recursion).
        ragflow_chunks = final_reference.get("chunks", []) or []
        if not ragflow_chunks:
            # Chat Completions retrieved no evidence — either our docs aren't
            # linked to the RAGFlow session, or the chat's internal retrieval
            # didn't find them.  Fall back to the direct retrieval API which
            # works reliably regardless of session/document linkage.
            try:
                strategy = request.strategy or self._default_strategy()
                logger.info("Chat Completions returned 0 chunks for collection %s — using retrieval-only", collection.id)
                fallback_chunks, _ = client.retrieve(
                    str(dataset_id),
                    request.question,
                    page_size=max(strategy.final_top_k, 6),
                )
                fallback_chunks = self._filter_ragflow_chunks_by_acl(request, collection, fallback_chunks)
                fallback_chunks = _prepare_chunks_for_answer(request.question, fallback_chunks)
                evidence_score = max((c.score for c in fallback_chunks), default=0.0)
                min_score = strategy.min_evidence_score if hasattr(strategy, 'min_evidence_score') else 0.22
                fallback_answer = _clean_answer_text(self.llm.answer(
                    request.question, fallback_chunks,
                    min_score,
                    history=request.history,
                ))
                fallback_citations = [_citation_from_retrieved_chunk(c) for c in fallback_chunks]
                # Persist the fallback answer so conversation history survives refresh
                fallback_trace = RetrievalTrace(
                    collection_id=request.collection_id,
                    query=request.question,
                    strategy={**strategy.model_dump(), "engine": "ragflow_stream_fallback"},
                    candidates=fallback_citations,
                    selected_context=fallback_citations,
                )
                self.db.add(fallback_trace)
                self.db.flush()
                fallback_entity = Answer(
                    trace_id=fallback_trace.id,
                    collection_id=request.collection_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    question=request.question,
                    answer=fallback_answer,
                    citations=fallback_citations,
                    model=f"{self.model_name} via RAGFlow retrieval",
                    evidence_score=evidence_score,
                )
                self.db.add(fallback_entity)
                self.db.commit()
                yield {"answer": fallback_answer, "reference": {"chunks": fallback_citations}, "final": True}
            except Exception as exc:
                logger.error("Retrieval-only fallback also failed: %s", exc)
                yield {"answer": "当前知识库没有足够的证据回答这个问题。", "reference": {}, "final": True}
            return

        citations = _map_ragflow_citations(ragflow_chunks)
        evidence_score = _compute_ragflow_evidence_score(ragflow_chunks)

        # Yield the final answer content + signal
        yield {
            "answer": final_answer,
            "reference": {"chunks": ragflow_chunks},
            "final": True,
        }

    # ------------------------------------------------------------------
    # Fallback: retrieval-only — RAGFlow retrieves, our LLM answers
    # ------------------------------------------------------------------

    def _ask_with_ragflow_retrieval_only(
        self,
        request: ChatRequest,
        collection: Collection,
        dataset_id: str,
    ) -> ChatResponse:
        client = RagFlowClient()
        strategy = request.strategy or self._default_strategy()
        query = request.question
        if strategy.use_query_rewrite and self.llm.provider != "mock":
            query = self._rewrite_for_ragflow(request.question, request.history)

        chunks, raw_retrieval = client.retrieve(
            str(dataset_id),
            query,
            page_size=max(strategy.final_top_k, 6),
        )
        raw_chunk_count = len(chunks)
        chunks = self._filter_ragflow_chunks_by_acl(request, collection, chunks)
        chunks = _prepare_chunks_for_answer(request.question, chunks)
        evidence_score = max([chunk.score for chunk in chunks], default=0.0)
        answer_text = _clean_answer_text(self.llm.answer(request.question, chunks, strategy.min_evidence_score, history=request.history))
        citations = [_citation_from_retrieved_chunk(chunk) for chunk in chunks]
        trace = RetrievalTrace(
            collection_id=request.collection_id,
            query=request.question,
            rewritten_query=query if query != request.question else None,
            strategy={
                **strategy.model_dump(),
                "engine": "ragflow",
                "ragflow_dataset_id": dataset_id,
                "acl_filtered_from": raw_chunk_count,
                "acl_filtered_to": len(chunks),
            },
            candidates=citations,
            selected_context=citations,
        )
        self.db.add(trace)
        self.db.flush()
        answer = Answer(
            trace_id=trace.id,
            collection_id=request.collection_id,
            user_id=request.user_id,
            session_id=request.session_id,
            question=request.question,
            answer=answer_text,
            citations=citations,
            model=f"{self.model_name} via RAGFlow retrieval",
            evidence_score=evidence_score,
        )
        self.db.add(answer)
        collection.metadata_ = {**(collection.metadata_ or {}), "last_ragflow_retrieval": raw_retrieval}
        self.db.commit()
        return ChatResponse(
            answer_id=answer.id,
            trace_id=trace.id,
            session_id=answer.session_id,
            answer=answer.answer,
            citations=citations,
            evidence_score=evidence_score,
            model=answer.model,
        )

    # ------------------------------------------------------------------
    # Query rewrite (used by retrieval-only path)
    # ------------------------------------------------------------------

    def _contextualize_question(self, question: str, history: list[ChatTurn]) -> str:
        if self.llm.provider == "mock":
            return question
        history_text = self._history_text(history)
        if not history_text:
            return self.llm.rewrite_query(question)
        fallback = self._fallback_context_query(question, history)
        prompt = (
            "Rewrite the user question into a standalone enterprise knowledge-base "
            "retrieval query using the recent conversation only for context. Preserve "
            "named entities, dates, project names, document titles, metrics, and "
            "technical terms. If the question is Chinese, include important English "
            "technical keywords too. Return only one line inside <query>...</query>. "
            "Do not include analysis, explanation, bullets, or thinking process.\n\n"
            f"Recent conversation:\n{history_text}\n\n"
            f"Current question: {question}"
        )
        try:
            raw = self.llm._chat(prompt, temperature=0.0, max_tokens=256)
            return self._clean_rewritten_query(raw, fallback)
        except Exception:
            return fallback

    def _rewrite_for_ragflow(self, question: str, history: list[ChatTurn]) -> str:
        history_text = self._history_text(history)
        fallback = self._fallback_context_query(question, history)
        prompt = (
            "Rewrite this user question into a concise retrieval query for a RAGFlow "
            "hybrid search index. Preserve named entities and numbers. If the question "
            "is Chinese, include important English technical keywords too. Use recent "
            "conversation only to resolve follow-up references. Return only one line "
            "inside <query>...</query>. Do not include analysis, explanation, bullets, "
            "or thinking process.\n\n"
            f"Recent conversation:\n{history_text or 'None'}\n\n"
            f"Question: {question}"
        )
        try:
            raw = self.llm._chat(prompt, temperature=0.0, max_tokens=256)
            return self._clean_rewritten_query(raw, fallback)
        except Exception:
            return fallback

    def _clean_rewritten_query(self, raw: str, fallback: str) -> str:
        text = (raw or "").strip()
        if not text:
            return fallback
        if "<query>" in text and "</query>" in text:
            text = text.split("<query>", 1)[1].split("</query>", 1)[0].strip()
        disallowed = ("thinking process", "analyze the request", "constraints:", "final answer")
        if any(marker in text.lower() for marker in disallowed):
            return fallback
        if "\n" in text:
            text = " ".join(line.strip(" -*\t") for line in text.splitlines() if line.strip())
        if len(text) > 260:
            return fallback
        return text or fallback

    def _fallback_context_query(self, question: str, history: list[ChatTurn]) -> str:
        user_turns = [turn.content for turn in history if turn.role == "user"]
        context = " ".join(user_turns[-2:] + [question])
        context = " ".join(context.split())
        return context[:320] or question

    def _history_text(self, history: list[ChatTurn], limit: int = 8) -> str:
        lines = []
        for turn in history[-limit:]:
            content = " ".join(turn.content.split())
            if len(content) > 700:
                content = content[:700] + "..."
            role = "User" if turn.role == "user" else "Assistant"
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _active_model(self) -> ModelConfig | None:
        return self.db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))

    def _llm_client(self, model_config: ModelConfig | None) -> LLMClient:
        if not model_config:
            return LLMClient()
        return LLMClient(
            provider=model_config.provider,
            base_url=model_config.base_url,
            api_key=model_config.api_key,
            model=model_config.model_name,
            temperature=model_config.temperature,
            max_tokens=model_config.max_tokens,
        )

    def _ensure_ragflow_chat(self, collection: Collection) -> str | None:
        """Create a RAGFlow chat for a legacy collection that lacks one, and
        persist the resulting chat_id back to collection.metadata_.

        Returns the chat_id string, or None on failure.
        """
        from app.ragflow.client import RagFlowClient, RagFlowError

        dataset_id = (collection.metadata_ or {}).get("ragflow_dataset_id")
        if not dataset_id:
            return None

        try:
            client = RagFlowClient()
            chat = client.create_chat(
                name=f"{collection.name}-chat",
                dataset_ids=[str(dataset_id)],
                llm_id=self.settings.ragflow_chat_model,
                top_n=6,
                similarity_threshold=0.1,
                vector_similarity_weight=0.3,
                prompt_config={
                    "system": "You are a helpful enterprise knowledge base assistant.",
                    "quote": True,
                    "refine_multiturn": True,
                },
            )
            chat_id = chat.get("id")
            if chat_id:
                collection.metadata_ = {
                    **(collection.metadata_ or {}),
                    "ragflow_chat_id": chat_id,
                    "ragflow_chat_name": chat.get("name"),
                }
                self.db.commit()
            return chat_id
        except RagFlowError:
            return None

    def _filter_ragflow_chunks_by_acl(self, request: ChatRequest, collection: Collection, chunks: list) -> list:
        principals = set(request.user_acl_principals or [])
        if {"admin", "maintainer"} & principals:
            return chunks
        readable = self._readable_documents(collection.id, principals)
        allowed_ids = {doc.id for doc in readable}
        allowed_ids.update(str((doc.metadata_ or {}).get("ragflow_document_id") or "") for doc in readable)
        allowed_ids = {item for item in allowed_ids if item}
        allowed_names = {doc.filename for doc in readable if doc.filename}
        allowed_names.update(doc.title for doc in readable if doc.title)

        filtered = []
        for chunk in chunks:
            raw = (getattr(chunk, "metadata", None) or {}).get("ragflow") or {}
            candidate_ids = {
                str(getattr(chunk, "document_id", "") or ""),
                str(raw.get("document_id") or ""),
                str(raw.get("doc_id") or ""),
            }
            candidate_names = {
                str(getattr(chunk, "title", "") or ""),
                str(raw.get("docnm_kwd") or ""),
                str(raw.get("document_keyword") or ""),
                str(raw.get("document_name") or ""),
                str(raw.get("filename") or ""),
            }
            if (candidate_ids & allowed_ids) or (candidate_names & allowed_names):
                filtered.append(chunk)
        return filtered

    def _can_use_unfiltered_ragflow_chat(self, request: ChatRequest, collection: Collection) -> bool:
        principals = set(request.user_acl_principals or [])
        if {"admin", "maintainer"} & principals:
            return True
        documents = self.db.scalars(select(Document).where(Document.collection_id == collection.id)).all()
        if not documents:
            return False
        readable = self._readable_documents(collection.id, principals)
        return len(readable) == len(documents)

    def _readable_documents(self, collection_id: str, principals: set[str]) -> list[Document]:
        documents = self.db.scalars(select(Document).where(Document.collection_id == collection_id)).all()
        readable = []
        for document in documents:
            acl = set(document.acl or ["public"])
            if "public" in acl or acl & principals:
                readable.append(document)
        return readable

    def _default_strategy(self) -> RetrievalStrategy:
        preset = self.db.scalar(select(RagStrategyPreset).where(RagStrategyPreset.is_default.is_(True)))
        if preset:
            return RetrievalStrategy(**preset.config)
        return RetrievalStrategy(
            dense_top_k=self.settings.retrieval_dense_top_k,
            keyword_top_k=self.settings.retrieval_keyword_top_k,
            final_top_k=self.settings.retrieval_final_top_k,
            use_reranker=self.settings.enable_reranker,
            use_parent_context=True,
            context_window_chunks=1,
            min_evidence_score=self.settings.min_answer_evidence_score,
        )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _map_ragflow_citations(ragflow_chunks: list[dict]) -> list[dict]:
    """Convert RAGFlow chunk reference format to our citation schema."""
    citations = []
    for chunk in ragflow_chunks:
        content = _chunk_content(chunk)
        title = _chunk_title(chunk)
        citations.append(
            {
                "chunk_id": str(chunk.get("chunk_id") or chunk.get("id") or ""),
                "document_id": str(chunk.get("doc_id") or chunk.get("document_id") or ""),
                "title": title,
                "title_path": _chunk_title_path(chunk, title),
                "score": float(chunk.get("similarity") or 0.0),
                "dense_score": _optional_float(chunk.get("vector_similarity")),
                "keyword_score": _optional_float(chunk.get("term_similarity")),
                "rerank_score": _optional_float(chunk.get("rerank_score")),
                "source_uri": chunk.get("source_uri"),
                "metadata": {"ragflow_chunk": _safe_citation_metadata(chunk)},
                "preview": _preview(content),
                "content": content,
                "page": chunk.get("page") or chunk.get("page_num") or chunk.get("position"),
            }
        )
    return citations


def _citation_from_retrieved_chunk(chunk) -> dict:
    raw = (chunk.metadata or {}).get("ragflow") or {}
    content = chunk.content or _chunk_content(raw)
    title = chunk.title if chunk.title and chunk.title != "RAGFlow document" else _chunk_title(raw)
    return {
        "chunk_id": chunk.chunk_id,
        "document_id": chunk.document_id,
        "title": title,
        "title_path": chunk.title_path or _chunk_title_path(raw, title),
        "score": chunk.score,
        "dense_score": chunk.dense_score,
        "keyword_score": chunk.keyword_score,
        "rerank_score": chunk.rerank_score,
        "source_uri": chunk.source_uri,
        "metadata": _safe_citation_metadata(chunk.metadata),
        "preview": _preview(content),
        "content": content,
        "page": raw.get("page") or raw.get("page_num") or raw.get("position"),
    }


def _compute_ragflow_evidence_score(ragflow_chunks: list[dict]) -> float:
    scores = [float(c.get("similarity") or 0.0) for c in ragflow_chunks]
    return max(scores, default=0.0)


def _safe_citation_metadata(metadata: dict | None) -> dict:
    data = dict(metadata or {})
    raw = data.get("ragflow")
    if isinstance(raw, dict):
        data["ragflow"] = _safe_citation_metadata(raw)
    for key in ("content", "content_with_weight", "text", "preview"):
        if key in data and data[key] is not None:
            data[key] = _preview(_readable_evidence_text(str(data[key])), 1000)
    return data


def _chunk_content(chunk: dict) -> str:
    value = (
        chunk.get("content_with_weight")
        or chunk.get("content")
        or chunk.get("text")
        or chunk.get("preview")
        or ""
    )
    return _readable_evidence_text(str(value))


def _chunk_title(chunk: dict) -> str:
    return str(
        chunk.get("docnm_kwd")
        or chunk.get("document_keyword")
        or chunk.get("document_name")
        or chunk.get("filename")
        or chunk.get("title")
        or "RAGFlow document"
    )


def _chunk_title_path(chunk: dict, title: str) -> list[str]:
    path = chunk.get("title_path") or chunk.get("section_path") or []
    if isinstance(path, list):
        values = [str(item) for item in path if str(item).strip()]
    else:
        values = [str(path)] if str(path or "").strip() else []
    return values or [title]


def _preview(content: str, limit: int = 520) -> str:
    text = " ".join((content or "").split())
    return text[:limit]


def _prepare_chunks_for_answer(question: str, chunks: list) -> list:
    for chunk in chunks:
        content = getattr(chunk, "content", "") or ""
        try:
            chunk.content = _focus_evidence_for_question(question, content)
        except Exception:
            chunk.content = _readable_evidence_text(content)
    return chunks


def _focus_evidence_for_question(question: str, content: str, limit: int = 5200) -> str:
    text = _readable_evidence_text(content)
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    is_row_like_table = any(" | " in row for row in rows)
    if len(rows) <= 3 and not is_row_like_table:
        return text if len(text) <= limit else text[:limit]
    terms = _query_focus_terms(question)
    selected: list[str] = []
    for index, row in enumerate(rows):
        if any(term in row for term in terms):
            selected.append(_trim_row_around_terms(row, terms))
    deduped: list[str] = []
    seen: set[str] = set()
    for row in selected:
        if row not in seen:
            deduped.append(row)
            seen.add(row)
    if not deduped:
        if len(text) <= limit:
            return text
        return text[:limit]
    focused = "\n".join(deduped)
    if len(focused) > limit:
        focused = focused[:limit]
    if is_row_like_table:
        guard = _table_guardrail_for_question(question, focused)
    else:
        guard = ""
    return (
        "以下是从长表格/长片段中按当前问题筛出的相关行。"
        "如果某个地区行没有明确给出旺季期间或上浮金额，不得从相邻地区推断。"
        f"{guard}\n"
        f"{focused}"
    )


def _table_guardrail_for_question(question: str, focused: str) -> str:
    asked_season_or_float = any(term in question for term in ("旺季", "浮动", "上浮", "调整"))
    if not asked_season_or_float:
        return ""
    has_explicit_field = any(term in focused for term in ("旺季", "浮动", "上浮", "调整"))
    if has_explicit_field:
        return ""
    return " 当前筛出的相关行没有出现旺季/浮动/上浮字段，回答时必须说明证据未提供具体浮动信息。"


def _trim_row_around_terms(row: str, terms: set[str], window: int = 420) -> str:
    if len(row) <= window * 2:
        return row
    positions = [row.find(term) for term in terms if term and row.find(term) >= 0]
    if not positions:
        return row[: window * 2]
    center = min(positions)
    start = max(0, center - window)
    end = min(len(row), center + window)
    snippet = row[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(row):
        snippet = snippet + "..."
    return snippet


def _query_focus_terms(question: str) -> set[str]:
    text = question or ""
    terms = {term for term in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", text) if len(term) >= 2}
    for token in re.findall(r"[\u4e00-\u9fff]{2}", text):
        terms.add(token)
    alias_terms: set[str] = set()
    aliases = {
        "新疆": ["新疆", "乌鲁木齐", "石河子", "克拉玛依", "昌吉", "伊犁", "阿克苏", "喀什", "哈密", "吐鲁番", "巴音郭楞", "博尔塔拉", "阿勒泰"],
        "青海": ["青海", "西宁", "玉树", "果洛", "海北", "黄南", "海东", "海南州", "海西"],
        "海南": ["海南", "海口", "三亚", "三沙", "儋州", "琼海", "文昌"],
        "西藏": ["西藏", "拉萨", "林芝", "山南", "日喀则", "昌都"],
    }
    for key, values in aliases.items():
        if key in text:
            alias_terms.update(values)
            terms.update(values)
    stopwords = {
        "哪些",
        "地区",
        "是否",
        "有没有",
        "多少",
        "怎么",
        "什么",
        "标准",
        "费用",
        "情况",
        "旺季",
        "期间",
        "浮动",
        "上浮",
        "调整",
        "住宿",
        "差旅",
    }
    focused_terms = {term for term in terms if term not in stopwords}
    if alias_terms:
        latin_or_numeric = {term for term in focused_terms if re.search(r"[A-Za-z0-9]", term)}
        return alias_terms | latin_or_numeric
    return focused_terms


def _readable_evidence_text(value: str) -> str:
    text = unescape(value or "")
    if "<table" in text.lower() or "<tr" in text.lower():
        parsed = _html_table_to_text(text)
        if parsed:
            return parsed
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|table)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _html_table_to_text(value: str) -> str:
    parser = _EvidenceTableParser()
    parser.feed(value)
    if not parser.rows:
        return ""
    lines = [" | ".join(cell for cell in row if cell) for row in parser.rows]
    return "\n".join(line for line in lines if line).strip()


class _EvidenceTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th", "caption"}:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th", "caption"} and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            if self._row is None:
                self._row = []
            if text:
                self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _clean_answer_text(answer: str) -> str:
    text = (answer or "").strip()
    if not text:
        return text
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)

    lines: list[str] = []
    previous = ""
    for line in text.splitlines():
        normalized = re.sub(r"\s+", " ", line).strip()
        if normalized and normalized == previous:
            continue
        lines.append(line.rstrip())
        previous = normalized
    text = "\n".join(lines).strip()

    paragraphs = re.split(r"\n\s*\n", text)
    seen: set[str] = set()
    unique: list[str] = []
    for paragraph in paragraphs:
        key = re.sub(r"\s+", " ", paragraph).strip()
        if key and key not in seen:
            unique.append(paragraph.strip())
            seen.add(key)
    return "\n\n".join(unique).strip()


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
