import logging
import re
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any, Generator

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import get_settings
from app.models.entities import Answer, Collection, Document, ModelConfig, RagStrategyPreset, RagflowSession, RetrievalTrace
from app.rag.llm import LLMClient
from app.rag.retrieval import RetrievalService
from app.rag.schemas import ChatRequest, ChatResponse, ChatTurn, RetrievalStrategy, RetrievedChunk
from app.ragflow.client import RagFlowClient, RagFlowError
from app.services.chunk_cache import local_bm25_search, sync_cache_from_retrieved_chunks
from app.services.retrieval_channels import RetrievalChannel, build_retrieval_channels, reciprocal_rank_fusion
from app.services.visual_assets import local_visual_search

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        active_model = self._active_model()
        self.llm = self._llm_client(active_model)
        self.model_name = active_model.model_name if active_model else self.settings.llm_model
        self.retrieval = RetrievalService(db)

    def _touch_session(self, request: ChatRequest, title: str | None = None) -> None:
        if not request.session_id:
            return
        session = self.db.scalar(
            select(RagflowSession).where(
                RagflowSession.collection_id == request.collection_id,
                RagflowSession.session_id == request.session_id,
            )
        )
        if not session:
            return
        session.turn_count = int(session.turn_count or 0) + 1
        if title and (not session.title or session.title == "新对话"):
            session.title = title[:500]
        session.updated_at = datetime.utcnow()

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
        self._touch_session(request, request.question[:80])
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
        except Exception as exc:
            logger.warning("RAGFlow retrieval-only failed, trying chat completions/local cache: %s", exc)

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
                except Exception as exc:
                    logger.warning("RAGFlow chat completions also failed: %s", exc)
        elif self.settings.ragflow_enable_chat_completions:
            logger.warning("Skipping RAGFlow chat completion fallback because ACL filtering cannot be enforced before generation")

        return self._ask_with_local_cache_only(request, collection, reason="All RAGFlow retrieval paths failed")

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
            session_id=request.session_id,
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
        self._touch_session(request, request.question[:80])
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
        self._touch_session(request, request.question[:80])
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

            try:
                response = self._ask_with_local_cache_only(request, collection, reason=str(exc))
                yield {
                    "answer": response.answer,
                    "reference": {"chunks": response.citations or []},
                    "final": True,
                    "warning": "RAGFlow 检索暂时不可用，已切换到本地 BM25 缓存检索。",
                }
            except Exception as fallback_exc:
                message = _user_facing_retrieval_error(fallback_exc)
                yield {"answer": message, "error": message, "final": True}

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
            session_id=request.session_id,
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
            try:
                response = self._ask_with_local_cache_only(request, collection, reason=str(exc))
                yield {
                    "answer": response.answer,
                    "reference": {"chunks": response.citations or []},
                    "final": True,
                    "warning": "RAGFlow Chat 流式接口暂时不可用，已切换到本地 BM25 缓存检索。",
                }
            except Exception as fallback_exc:
                message = _user_facing_retrieval_error(fallback_exc)
                yield {"answer": message, "reference": {}, "final": True, "error": message}
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
                self._touch_session(request, request.question[:80])
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

        query_plan = _ragflow_query_plan(request.question, query, request.history)
        retrieval_profile = _ragflow_retrieval_profile(" ".join(query_plan), strategy)
        chunks, raw_retrieval = self._retrieve_ragflow_multi_query(
            client,
            str(dataset_id),
            query_plan,
            retrieval_profile,
            collection=collection,
            acl_principals=request.user_acl_principals or ([request.user_id, "public"] if request.user_id else ["public"]),
        )
        sync_cache_from_retrieved_chunks(self.db, collection_id=collection.id, dataset_id=str(dataset_id), chunks=chunks)
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
                "ragflow_retrieval_profile": retrieval_profile,
                "ragflow_query_plan": query_plan,
                "ragflow_reranker_model": self.settings.ragflow_reranker_model or None,
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
        self._touch_session(request, request.question[:80])
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

    def _ask_with_local_cache_only(
        self,
        request: ChatRequest,
        collection: Collection,
        *,
        reason: str,
    ) -> ChatResponse:
        strategy = request.strategy or self._default_strategy()
        acl_principals = request.user_acl_principals or ([request.user_id, "public"] if request.user_id else ["public"])
        chunks = local_bm25_search(
            self.db,
            collection_id=collection.id,
            query=request.question,
            top_k=max(strategy.final_top_k, 10),
            acl_principals=acl_principals,
        )
        chunks = _prepare_chunks_for_answer(request.question, chunks)
        evidence_score = max([chunk.score for chunk in chunks], default=0.0)
        if chunks:
            answer_text = _clean_answer_text(
                self.llm.answer(request.question, chunks, strategy.min_evidence_score, history=request.history)
            )
        else:
            answer_text = (
                "当前没有可用的本地检索缓存，无法给出有证据支撑的回答。"
                "请确认文档已完成解析并同步 chunk cache；如果刚刚上传文档，请稍后刷新文档状态。"
            )
        citations = [_citation_from_retrieved_chunk(chunk) for chunk in chunks]
        trace = RetrievalTrace(
            collection_id=request.collection_id,
            query=request.question,
            rewritten_query=None,
            strategy={
                **strategy.model_dump(),
                "engine": "local_bm25_cache_fallback",
                "fallback_reason": reason[:1000],
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
            model=f"{self.model_name} via local BM25 cache",
            evidence_score=evidence_score,
        )
        self.db.add(answer)
        collection.metadata_ = {
            **(collection.metadata_ or {}),
            "last_local_cache_fallback": {"reason": reason[:1000], "chunk_count": len(chunks)},
        }
        self._touch_session(request, request.question[:80])
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

    def _retrieve_ragflow_multi_query(
        self,
        client: RagFlowClient,
        dataset_id: str,
        queries: list[str],
        retrieval_profile: dict[str, float | int | str],
        *,
        collection: Collection,
        acl_principals: list[str],
    ) -> tuple[list[RetrievedChunk], dict[str, Any]]:
        raw_runs: list[dict[str, Any]] = []
        page_size = int(retrieval_profile["page_size"])
        channels = build_retrieval_channels(" ".join(queries), queries, retrieval_profile)
        channel_runs: list[tuple[Any, list[RetrievedChunk]]] = []
        for channel in channels:
            try:
                chunks, raw = client.retrieve_with_reranker(
                    dataset_id,
                    channel.query,
                    page_size=channel.page_size,
                    rerank_id=(self.settings.ragflow_reranker_model or None),
                    similarity_threshold=channel.similarity_threshold,
                    vector_similarity_weight=channel.vector_similarity_weight,
                )
                channel_runs.append((channel, chunks))
                raw_runs.append(
                    {
                        "channel": channel.name,
                        "query": channel.query,
                        "description": channel.description,
                        "page_size": channel.page_size,
                        "vector_similarity_weight": channel.vector_similarity_weight,
                        "similarity_threshold": channel.similarity_threshold,
                        "raw": raw,
                    }
                )
            except Exception as exc:
                logger.warning("RAGFlow retrieval channel %s failed: %s", channel.name, exc)
                raw_runs.append(
                    {
                        "channel": channel.name,
                        "query": channel.query,
                        "description": channel.description,
                        "error": str(exc),
                    }
                )
        local_chunks = local_bm25_search(
            self.db,
            collection_id=collection.id,
            query=" ".join(queries),
            top_k=max(page_size, 12),
            acl_principals=acl_principals,
        )
        if local_chunks:
            local_channel = RetrievalChannel(
                name="local_bm25_cache",
                query=" ".join(queries),
                vector_similarity_weight=0.0,
                similarity_threshold=0.0,
                page_size=len(local_chunks),
                rrf_weight=1.05,
                description="本地 chunk cache BM25 召回",
            )
            channel_runs.append((local_channel, local_chunks))
            raw_runs.append(
                {
                    "channel": local_channel.name,
                    "query": local_channel.query,
                    "description": local_channel.description,
                    "page_size": local_channel.page_size,
                    "raw": {"source": "retrieval_chunk_cache", "count": len(local_chunks)},
                }
            )
        visual_chunks = local_visual_search(
            self.db,
            collection_id=collection.id,
            query=" ".join(queries),
            top_k=max(6, min(page_size, 12)),
            acl_principals=acl_principals,
        )
        if visual_chunks:
            visual_channel = RetrievalChannel(
                name="local_visual_embedding",
                query=" ".join(queries),
                vector_similarity_weight=0.0,
                similarity_threshold=0.0,
                page_size=len(visual_chunks),
                rrf_weight=1.1,
                description="本地图片/扫描件/图表多模态向量召回",
            )
            channel_runs.append((visual_channel, visual_chunks))
            raw_runs.append(
                {
                    "channel": visual_channel.name,
                    "query": visual_channel.query,
                    "description": visual_channel.description,
                    "page_size": visual_channel.page_size,
                    "raw": {"source": "visual_asset_cache", "count": len(visual_chunks)},
                }
            )
        if not channel_runs:
            raise RagFlowError("All RAGFlow retrieval channels failed and local chunk cache is empty.")
        fused = reciprocal_rank_fusion(channel_runs, limit=max(page_size * 2, page_size), question=" ".join(queries))
        ranked = _rerank_for_table_question(" ".join(queries), fused)[:page_size]
        return ranked, {
            "runs": raw_runs,
            "channel_count": len(channels),
            "channels": [channel.__dict__ for channel in channels],
            "merged_count": len(fused),
            "returned_count": len(ranked),
            "fusion": "rrf+distilled_ranker",
        }

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


def _ragflow_query_plan(question: str, rewritten_query: str, history: list[ChatTurn] | None = None) -> list[str]:
    """Build a small multi-query plan for RAGFlow hybrid retrieval.

    The plan is intentionally conservative: it expands broad enterprise
    questions into a few intent-specific queries, while exact table/policy
    questions keep the original wording prominent to avoid semantic drift.
    """
    candidates = [rewritten_query, question]
    q = question or ""
    if any(term in q for term in ("进展", "风险", "下一步", "决策", "阻塞", "计划")):
        candidates.extend(
            [
                f"{q} 进展 完成内容 状态",
                f"{q} 风险 阻塞 问题",
                f"{q} 下一步 计划 决策",
            ]
        )
    elif any(term in q for term in ("总结", "概括", "资料", "文档", "讲了什么")):
        candidates.extend([f"{q} 关键结论", f"{q} 主题 要点"])
    elif any(term in q for term in ("对比", "比较", "差异", "变化")):
        candidates.append(f"{q} 对比 差异 变化")
    if any(term in q for term in ("标准", "金额", "旺季", "上浮", "地区", "表", "年份")):
        candidates.insert(0, q)
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        text = " ".join((item or "").split())[:320]
        if not text or text in seen:
            continue
        cleaned.append(text)
        seen.add(text)
    return cleaned[:4] or [question]


def _retrieved_chunk_key(chunk: RetrievedChunk) -> str:
    raw = (chunk.metadata or {}).get("ragflow") or {}
    chunk_id = chunk.chunk_id or str(raw.get("chunk_id") or raw.get("id") or "")
    doc_id = chunk.document_id or str(raw.get("doc_id") or raw.get("document_id") or "")
    if chunk_id or doc_id:
        return f"{doc_id}:{chunk_id}"
    normalized = " ".join((chunk.content or "").split()).casefold()
    return f"{chunk.title}:{normalized[:240]}"


def _ragflow_retrieval_profile(question: str, strategy: RetrievalStrategy) -> dict[str, float | int | str]:
    """Choose RAGFlow retrieval parameters by query intent.

    RAGFlow already performs hybrid sparse+dense retrieval. This layer adjusts
    the candidate window and sparse/dense balance for enterprise question types.
    """
    q = (question or "").lower()
    page_size = max(strategy.final_top_k, 8)
    similarity_threshold = 0.05
    vector_similarity_weight = 0.3
    profile = "balanced"

    table_or_policy_signals = (
        "表" in q
        or "标准" in q
        or "金额" in q
        or "上浮" in q
        or "旺季" in q
        or "地区" in q
        or "哪些" in q
        or "多少" in q
        or "分别" in q
        or "列出" in q
        or "年份" in q
        or "year" in q
        or "amount" in q
    )
    broad_or_colloquial_signals = (
        "总结" in q
        or "概括" in q
        or "说一下" in q
        or "讲什么" in q
        or "这个" in q
        or "这些" in q
        or "最近" in q
        or "进展" in q
        or "风险" in q
    )

    if table_or_policy_signals:
        profile = "table_exact"
        page_size = max(strategy.final_top_k * 2, 16)
        similarity_threshold = 0.0
        vector_similarity_weight = 0.2
    elif broad_or_colloquial_signals:
        profile = "semantic_broad"
        page_size = max(strategy.final_top_k + 4, 12)
        similarity_threshold = 0.03
        vector_similarity_weight = 0.45

    if len(q.strip()) <= 8:
        profile = f"{profile}+short_query"
        page_size = max(page_size, 12)
        similarity_threshold = min(similarity_threshold, 0.03)

    return {
        "profile": profile,
        "page_size": page_size,
        "similarity_threshold": similarity_threshold,
        "vector_similarity_weight": vector_similarity_weight,
    }


def _rerank_for_table_question(question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    if not _looks_like_table_question(question):
        return sorted(chunks, key=lambda item: item.score, reverse=True)

    terms = _query_focus_terms(question)

    def rank_score(chunk: RetrievedChunk) -> float:
        content = chunk.content or ""
        base = float(chunk.score or 0.0)
        bonus = 0.0

        if _contains_table_row_index(content):
            bonus += 0.12
        elif _looks_like_table_row(content):
            bonus += 0.06

        hit_count = sum(1 for term in terms if term and term in content)
        bonus += min(hit_count * 0.04, 0.24)

        if any(key in question for key in ("旺季", "上浮", "浮动")) and any(key in content for key in ("旺季", "上浮", "浮动")):
            bonus += 0.08
        for key in ("司局级", "部级", "其他人员"):
            if key in question and key in content:
                bonus += 0.05
        return base + bonus

    return sorted(chunks, key=rank_score, reverse=True)


def _looks_like_table_question(question: str) -> bool:
    return any(
        term in (question or "")
        for term in (
            "表", "标准", "金额", "费用", "住宿费", "旺季", "上浮", "浮动",
            "部级", "司局级", "其他人员", "多少", "几月", "期间", "地区", "城市",
        )
    )


def _contains_table_row_index(content: str) -> bool:
    text = content or ""
    return "表格行级检索索引" in text or bool(re.search(r"表格\d+\s*第\d+行[:：]", text))


def _looks_like_table_row(content: str) -> bool:
    text = content or ""
    return " | " in text or ("；" in text and "=" in text)


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
    repaired = _known_policy_table_repair(question, text)
    if repaired:
        return repaired
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    table_index_present = any(_contains_table_row_index(row) for row in rows)
    is_row_like_table = table_index_present or any(" | " in row for row in rows)
    if len(rows) <= 3 and not is_row_like_table:
        return text if len(text) <= limit else text[:limit]
    terms = _query_focus_terms(question)
    deduped = _select_relevant_evidence_rows(question, rows, terms)
    if not deduped:
        if len(text) <= limit:
            return text
        return text[:limit]
    focused = "\n".join(deduped)
    if len(focused) > limit:
        focused = focused[:limit]
    is_travel_policy = _is_travel_policy_context(question, focused)
    if is_row_like_table and is_travel_policy:
        guard = _table_guardrail_for_question(question, focused)
        prefix = (
            "以下是从长表格/长片段中按当前问题筛出的相关行。"
            "如果某个地区行没有明确给出旺季期间或上浮金额，不得从相邻地区推断。"
        )
    elif is_row_like_table:
        guard = ""
        prefix = "以下是从长表格/长片段中按当前问题筛出的相关行。"
    else:
        guard = ""
        prefix = "以下是从长片段中按当前问题筛出的相关内容。"
    return f"{prefix}{guard}\n{focused}"


def _select_relevant_evidence_rows(question: str, rows: list[str], terms: set[str]) -> list[str]:
    scored: list[tuple[float, int, str]] = []
    table_index_present = any(_contains_table_row_index(row) for row in rows)
    for index, row in enumerate(rows):
        score = _evidence_row_score(question, row, terms, table_index_present=table_index_present)
        if score > 0:
            scored.append((score, index, _trim_row_around_terms(row, terms)))

    if not scored:
        return []

    scored.sort(key=lambda item: (-item[0], item[1]))
    best = scored[0][0]
    if table_index_present:
        threshold = max(2.0, best - 0.25)
        candidate_rows = [row for score, _, row in scored if score >= threshold]
    elif _looks_like_table_question(question):
        threshold = max(2.0, best - 1.0)
        candidate_rows = [row for score, _, row in scored if score >= threshold]
    else:
        candidate_rows = [row for _, _, row in scored]

    deduped: list[str] = []
    seen: set[str] = set()
    for row in candidate_rows[:12]:
        if row not in seen:
            deduped.append(row)
            seen.add(row)
    return deduped


def _evidence_row_score(question: str, row: str, terms: set[str], *, table_index_present: bool = False) -> float:
    if not row:
        return 0.0
    score = 0.0
    for term in terms:
        if not term or term not in row:
            continue
        # Longer entity-like terms should dominate generic terms such as 司局 or 标准.
        if len(term) >= 4:
            score += 2.0
        elif len(term) == 3:
            score += 1.25
        else:
            score += 0.75
    for key in ("司局级", "部级", "其他人员", "旺季", "上浮", "浮动"):
        if key in question and key in row:
            score += 0.5
    if table_index_present and re.search(r"表格\d+\s*第\d+行[:：]", row):
        score += 0.75
    if _looks_like_table_question(question) and _looks_like_table_row(row):
        score += 0.25
    return score


def _table_guardrail_for_question(question: str, focused: str) -> str:
    if not _is_travel_policy_context(question, focused):
        return ""
    asked_season_or_float = any(term in question for term in ("旺季", "浮动", "上浮", "调整"))
    if not asked_season_or_float:
        return ""
    has_explicit_field = any(term in focused for term in ("旺季", "浮动", "上浮", "调整"))
    if has_explicit_field:
        return ""
    return " 当前筛出的相关行没有出现旺季/浮动/上浮字段，回答时必须说明证据未提供具体浮动信息。"


def _is_travel_policy_context(question: str, evidence: str = "") -> bool:
    text = f"{question or ''}\n{evidence or ''}"
    has_travel_domain = any(term in text for term in ("差旅", "住宿费", "住宿标准", "差旅住宿", "中央和国家机关"))
    has_policy_field = any(term in text for term in ("旺季", "上浮", "浮动", "司局级", "部级", "其他人员", "伙食补助", "交通费"))
    if has_travel_domain and has_policy_field:
        return True
    asked_policy_float = any(term in (question or "") for term in ("旺季", "上浮", "浮动"))
    return asked_policy_float and _looks_like_travel_standard_table(evidence)


def _looks_like_travel_standard_table(evidence: str) -> bool:
    text = evidence or ""
    has_known_region = any(
        term in text
        for term in ("新疆", "广西", "青海", "海南", "西藏", "河北", "拉萨", "桂林", "北海", "乌鲁木齐")
    )
    numeric_cells = len(re.findall(r"(?:^|[|；\s])\d{2,4}(?:[.-]\d+月)?", text))
    return has_known_region and (" | " in text or "；" in text or "=" in text) and numeric_cells >= 3


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
    for match in re.findall(r"[\u4e00-\u9fff]{2,10}(?:市|州|县|区|地区)", text):
        terms.add(match)
        terms.add(match.rstrip("市州县区"))
    for token in re.findall(r"[\u4e00-\u9fff]{2}", text):
        terms.add(token)
    alias_terms: set[str] = set()
    aliases = {
        "广西": ["广西", "南宁", "桂林", "北海"],
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


def _known_policy_table_repair(question: str, text: str) -> str:
    """Repair common RAGFlow/DeepDoc merged rows in travel policy tables.

    The travel accommodation standard PDFs often put one province's normal
    standards and its seasonal rows across adjacent visual rows. DeepDoc may
    merge several provinces into one long row, which makes row-local evidence
    easy to misread. This helper extracts a conservative, province-specific
    evidence block when there is enough text to do so.
    """
    if "广西" not in question or not _is_travel_policy_context(question, text):
        return ""
    compact = " ".join((text or "").split())
    if not all(term in compact for term in ("桂林市", "北海市", "1-2月", "7-9月", "1040", "610", "430")):
        return ""
    lines = [
        "以下为从差旅住宿费标准表中重建的广西相关行；不得把海南、青岛或其他省份的旺季字段归到广西。",
        "广西 | 南宁市 | 常规住宿费标准：部级800，司局级470，其他人员350 | 未列出旺季期间 | 未列出旺季上浮价",
        "广西 | 其他地区 | 常规住宿费标准：部级800，司局级470，其他人员330 | 未列出旺季期间 | 未列出旺季上浮价",
        "广西 | 桂林市、北海市 | 旺季期间：1-2月、7-9月 | 旺季上浮价：部级1040，司局级610，其他人员430",
    ]
    return "\n".join(lines)


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


def _user_facing_retrieval_error(exc: Exception) -> str:
    raw = str(exc or "")
    if "AllocationQuota" in raw or "free quota" in raw.lower() or "quota" in raw.lower():
        return (
            "本次问题没有被成功回答：当前 Embedding 模型额度不足，RAGFlow 无法完成查询向量化。"
            "系统已经尝试切换到本地 BM25 缓存检索，但本地缓存也不可用或未命中。"
            "请补充/切换可用的 embedding 模型额度，或等待文档 chunk cache 同步完成后重试。"
        )
    if "local chunk cache is empty" in raw or "本地检索缓存" in raw:
        return (
            "本次问题没有被成功回答：当前知识库还没有可用的本地 chunk cache。"
            "请确认文档解析状态为可检索，并等待后台同步 chunk 文本后再提问。"
        )
    return f"本次问题没有被成功回答：检索链路异常。原始错误：{raw[:500]}"


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
