from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import get_settings
from app.models.entities import Answer, ModelConfig, RagStrategyPreset, RetrievalTrace
from app.rag.llm import LLMClient
from app.rag.retrieval import RetrievalService
from app.rag.schemas import ChatRequest, ChatResponse, RetrievalStrategy


class ChatService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        active_model = self._active_model()
        self.llm = self._llm_client(active_model)
        self.model_name = active_model.model_name if active_model else self.settings.llm_model
        self.retrieval = RetrievalService(db)

    def ask(self, request: ChatRequest) -> ChatResponse:
        strategy = request.strategy or self._default_strategy()
        rewritten = self.llm.rewrite_query(request.question) if strategy.use_query_rewrite else request.question
        chunks = self.retrieval.retrieve(
            request.collection_id,
            rewritten,
            strategy,
            acl_principals=request.user_acl_principals or ([request.user_id, "public"] if request.user_id else ["public"]),
        )
        evidence_score = max([chunk.score for chunk in chunks], default=0.0)
        answer_text = self.llm.answer(request.question, chunks, strategy.min_evidence_score)
        citations = [
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "title": chunk.title,
                "title_path": chunk.title_path,
                "score": chunk.score,
                "dense_score": chunk.dense_score,
                "keyword_score": chunk.keyword_score,
                "rerank_score": chunk.rerank_score,
                "source_uri": chunk.source_uri,
                "metadata": chunk.metadata,
                "preview": chunk.content[:300],
            }
            for chunk in chunks
        ]
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
            answer=answer.answer,
            citations=citations,
            evidence_score=evidence_score,
            model=answer.model,
        )

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
