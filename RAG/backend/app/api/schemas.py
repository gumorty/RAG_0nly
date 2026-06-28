from pydantic import BaseModel, EmailStr, Field


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    metadata: dict = Field(default_factory=dict)


class CollectionOut(BaseModel):
    id: str
    name: str
    description: str | None
    metadata: dict


class ChatSessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=500)


class ChatSessionOut(BaseModel):
    session_id: str
    collection_id: str
    title: str | None
    turn_count: int
    created_at: str
    updated_at: str


class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(pattern="^(admin|maintainer|member|viewer)$")
    api_key: str = Field(min_length=16, max_length=200)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    is_active: bool


class RegisterIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=10, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20)


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class DocumentOut(BaseModel):
    id: str
    collection_id: str
    title: str
    filename: str
    status: str
    error_message: str | None
    author: str | None
    project: str | None
    meeting_date: str | None
    tags: list[str]
    status_message: str | None = None
    ragflow_progress: float | None = None
    chunk_count: int | None = None
    token_count: int | None = None
    parser_engine: str | None = None
    parse_quality_score: float | None = None
    parse_quality_warnings: list[str] = Field(default_factory=list)


class ImportBatchOut(BaseModel):
    id: str
    collection_id: str
    source_type: str
    source_name: str
    status: str
    total_items: int
    imported_items: int
    skipped_items: int
    failed_items: int
    report: dict


class DocumentAnalysisOut(BaseModel):
    document: DocumentOut
    analysis: dict
    metadata: dict


class CollectionQualityOut(BaseModel):
    collection_id: str
    document_count: int
    ready_count: int
    failed_count: int
    total_chunks: int
    avg_chunks_per_ready_document: float
    warning_counts: dict[str, int]
    top_terms: list[tuple[str, int]]
    avg_parse_quality_score: float = 0.0
    parser_engine_counts: dict[str, int] = Field(default_factory=dict)


class AdminMetricsOut(BaseModel):
    collection_count: int
    document_count: int
    document_status_counts: dict[str, int]
    failed_documents: list[dict]
    chunk_count: int
    answer_count: int
    feedback_counts: dict[str, int]
    low_evidence_answer_count: int
    import_batch_count: int
    audit_action_counts: dict[str, int]
    recent_questions: list[dict]


class KnowledgeGapOut(BaseModel):
    id: str
    collection_id: str
    question: str
    reason: str
    evidence_score: float
    feedback: str | None
    citations: list[dict]
    created_at: str


class AnswerOut(BaseModel):
    id: str
    trace_id: str
    collection_id: str
    user_id: str | None = None
    session_id: str | None = None
    question: str
    answer: str
    citations: list[dict]
    model: str
    evidence_score: float
    feedback: str | None
    created_at: str


class AuditLogOut(BaseModel):
    id: str
    actor_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    metadata: dict
    created_at: str


class MeetingSummaryOut(BaseModel):
    collection_id: str
    meeting_date: str | None
    document_count: int
    authors: list[str]
    projects: list[str]
    progress: list[str]
    risks: list[str]
    next_steps: list[str]
    decisions: list[str]
    key_points: list[str] = Field(default_factory=list)


class StrategyCompareIn(BaseModel):
    strategies: dict[str, dict]


class StrategyCompareOut(BaseModel):
    collection_id: str
    results: list[dict]
    winner: dict | None


class FeedbackIn(BaseModel):
    feedback: str = Field(pattern="^(positive|negative|incorrect|missing_source)$")


class EvalCaseCreate(BaseModel):
    collection_id: str
    question: str = Field(min_length=1, max_length=4000)
    expected_answer: str | None = None
    expected_chunk_ids: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class UrlIngestCreate(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    author: str | None = None
    project: str | None = None
    meeting_date: str | None = None
    tags: list[str] = Field(default_factory=list)
    acl: list[str] = Field(default_factory=list)


class StrategyPresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    config: dict = Field(default_factory=dict)
    is_default: bool = False


class StrategyPresetOut(BaseModel):
    id: str
    name: str
    description: str | None
    config: dict
    is_default: bool


class ModelConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(default="openai_compatible", min_length=1, max_length=80)
    model_name: str = Field(min_length=1, max_length=200)
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1600, ge=1, le=128000)
    active: bool = True
    metadata: dict = Field(default_factory=dict)


class ModelConfigOut(BaseModel):
    id: str
    name: str
    provider: str
    model_name: str
    base_url: str | None
    api_key_masked: str | None
    temperature: float
    max_tokens: int
    active: bool
