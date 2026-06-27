from pydantic import BaseModel, Field


class ParsedDocument(BaseModel):
    title: str
    text: str
    sections: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class ChunkCandidate(BaseModel):
    content: str
    normalized_content: str
    title_path: list[str] = Field(default_factory=list)
    parent_chunk_id: str | None = None
    token_count: int
    metadata: dict = Field(default_factory=dict)


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    content: str
    title_path: list[str] = Field(default_factory=list)
    source_uri: str | None = None
    score: float
    dense_score: float | None = None
    keyword_score: float | None = None
    rerank_score: float | None = None
    metadata: dict = Field(default_factory=dict)


class RetrievalStrategy(BaseModel):
    dense_top_k: int
    keyword_top_k: int
    final_top_k: int
    use_query_rewrite: bool = True
    use_reranker: bool = False
    use_parent_context: bool = True
    context_window_chunks: int = Field(default=1, ge=0, le=3)
    min_evidence_score: float = 0.22


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    collection_id: str
    question: str = Field(min_length=1, max_length=4000)
    user_id: str | None = None
    session_id: str | None = Field(default=None, max_length=80)
    user_acl_principals: list[str] = Field(default_factory=list)
    strategy: RetrievalStrategy | None = None
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)


class ChatResponse(BaseModel):
    answer_id: str
    trace_id: str
    session_id: str | None = None
    answer: str
    citations: list[dict]
    evidence_score: float
    model: str
