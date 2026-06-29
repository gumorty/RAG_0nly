import json
import logging
import re
import uuid
from collections import Counter
from datetime import datetime
from pathlib import PurePath

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import can_read_acl, get_current_user, require_roles, write_audit
from app.api.schemas import (
    AdminMetricsOut,
    AnswerOut,
    AuditLogOut,
    ChatSessionCreate,
    ChatSessionOut,
    CollectionCreate,
    CollectionOut,
    CollectionQualityOut,
    DocumentAnalysisOut,
    DocumentOut,
    EvalCaseCreate,
    FeedbackIn,
    ImportBatchOut,
    KnowledgeGapOut,
    LoginIn,
    MeetingSummaryOut,
    ModelConfigCreate,
    ModelConfigOut,
    RefreshIn,
    RegisterIn,
    StrategyPresetCreate,
    StrategyCompareIn,
    StrategyCompareOut,
    StrategyPresetOut,
    TokenPairOut,
    UrlIngestCreate,
    UserCreate,
    UserOut,
)
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.core.db import get_db
from app.models.entities import (
    Answer,
    AuditLog,
    Chunk,
    Collection,
    Document,
    DocumentStatus,
    DomainLexiconTerm,
    EvalCase,
    EvaluationCase,
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
    ImportBatch,
    ModelConfig,
    RagflowSession,
    RagStrategyPreset,
    RetrievalChunkCache,
    RetrievalTrace,
    User,
    UserRole,
)
from app.rag.evaluation import EvaluationService
from app.rag.eval_metrics import citation_readability_rate, retrieval_metrics
from app.rag.normalize import sha256_bytes
from app.rag.schemas import ChatRequest, ChatResponse, RetrievalStrategy
from app.ragflow.chunk_method import select_chunk_method
from app.ragflow.client import RagFlowClient, RagFlowError
from app.services.batch_ingest import read_zip_documents
from app.services.parser_router import prepare_for_ragflow
from app.services.chunk_cache import local_bm25_search, sync_ready_document_chunks
from app.workers.ragflow_ingestion import start_background_monitor

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _normalize_username(value: str | None) -> str:
    username = (value or "").strip().casefold()
    if not username:
        raise HTTPException(status_code=422, detail="用户名不能为空")
    return username


def _is_collection_admin(user: User) -> bool:
    return user.role in {UserRole.admin, UserRole.maintainer}


def _collection_owner_id(collection: Collection) -> str | None:
    return str((collection.metadata_ or {}).get("owner_id") or "") or None


def _can_access_collection(user: User, collection: Collection) -> bool:
    if _is_collection_admin(user):
        return True
    metadata = collection.metadata_ or {}
    owner_id = _collection_owner_id(collection)
    if owner_id:
        return owner_id == user.id
    acl = metadata.get("acl") or []
    return bool(set([user.id, user.email, user.role.value, "public"]).intersection(set(acl)))


def _require_collection_access(user: User, collection: Collection, *, write: bool = False) -> None:
    if not _can_access_collection(user, collection):
        raise HTTPException(status_code=403, detail="No permission to access this collection")
    if write and not _is_collection_admin(user) and _collection_owner_id(collection) != user.id:
        raise HTTPException(status_code=403, detail="No permission to modify this collection")


@router.post("/auth/register", response_model=TokenPairOut)
def register(payload: RegisterIn, db: Session = Depends(get_db)) -> TokenPairOut:
    validate_password_strength(payload.password)
    username = _normalize_username(payload.username)
    existing = db.scalar(select(User).where(User.email == username))
    if existing:
        raise HTTPException(status_code=409, detail="该用户名已经注册")
    total_users = db.query(User).count()
    if total_users > 0 and not get_settings().public_registration_enabled:
        raise HTTPException(status_code=403, detail="当前系统未开放公开注册，请联系管理员创建账号")
    user = User(
        email=username,
        name=(payload.name or username).strip() or username,
        role=UserRole.admin if total_users == 0 else UserRole.member,
        api_key_hash=hash_api_key(generate_api_key()),
        password_hash=hash_password(payload.password),
        token_version=1,
        last_login_at=datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    return _token_pair(user)


@router.post("/auth/login", response_model=TokenPairOut)
def login(payload: LoginIn, db: Session = Depends(get_db)) -> TokenPairOut:
    username = _normalize_username(payload.username or payload.email)
    user = db.scalar(select(User).where(User.email == username, User.is_active.is_(True)))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    user.last_login_at = datetime.utcnow()
    db.commit()
    return _token_pair(user)


@router.post("/auth/refresh", response_model=TokenPairOut)
def refresh_token(payload: RefreshIn, db: Session = Depends(get_db)) -> TokenPairOut:
    token_payload = decode_token(payload.refresh_token, "refresh")
    user = db.scalar(select(User).where(User.id == token_payload.get("sub"), User.is_active.is_(True)))
    if not user or int(token_payload.get("ver", 0)) != int(user.token_version or 1):
        raise HTTPException(status_code=401, detail="刷新令牌无效")
    return _token_pair(user)


@router.post("/auth/logout")
def logout(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    current_user.token_version = int(current_user.token_version or 1) + 1
    write_audit(db, current_user, "auth.logout", "user", current_user.id, None)
    db.commit()
    return {"status": "ok"}


@router.get("/model-configs", response_model=list[ModelConfigOut])
def list_model_configs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ModelConfigOut]:
    models = db.scalars(select(ModelConfig).order_by(ModelConfig.created_at.desc())).all()
    return [_model_config_out(model) for model in models]


@router.post("/model-configs", response_model=ModelConfigOut)
def create_model_config(
    payload: ModelConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> ModelConfigOut:
    existing = db.scalar(select(ModelConfig).where(ModelConfig.name == payload.name))
    if existing:
        raise HTTPException(status_code=409, detail="Model config name already exists")
    if payload.active:
        for model in db.scalars(select(ModelConfig).where(ModelConfig.active.is_(True))).all():
            model.active = False
    model = ModelConfig(
        name=payload.name,
        provider=payload.provider,
        model_name=payload.model_name,
        base_url=payload.base_url,
        api_key=payload.api_key,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        active=payload.active,
        metadata_=payload.metadata,
    )
    db.add(model)
    write_audit(db, current_user, "model_config.create", "model_config", model.id, {"name": model.name})
    db.commit()
    return _model_config_out(model)


@router.post("/model-configs/{model_id}/activate", response_model=ModelConfigOut)
def activate_model_config(
    model_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> ModelConfigOut:
    target = db.scalar(select(ModelConfig).where(ModelConfig.id == model_id))
    if not target:
        raise HTTPException(status_code=404, detail="Model config not found")
    for model in db.scalars(select(ModelConfig)).all():
        model.active = model.id == model_id
    write_audit(db, current_user, "model_config.activate", "model_config", target.id, {"name": target.name})
    db.commit()
    return _model_config_out(target)


@router.delete("/model-configs/{model_id}")
def delete_model_config(
    model_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> dict:
    target = db.scalar(select(ModelConfig).where(ModelConfig.id == model_id))
    if not target:
        raise HTTPException(status_code=404, detail="Model config not found")
    write_audit(db, current_user, "model_config.delete", "model_config", target.id, {"name": target.name})
    db.delete(target)
    db.commit()
    return {"status": "deleted", "model_id": model_id}


@router.get("/admin/metrics", response_model=AdminMetricsOut)
def admin_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> AdminMetricsOut:
    collections = db.scalars(select(Collection)).all()
    documents = db.scalars(select(Document)).all()
    answers = db.scalars(select(Answer).order_by(Answer.created_at.desc())).all()
    audit_logs = db.scalars(select(AuditLog)).all()

    status_counts: Counter[str] = Counter(_status_value(document) for document in documents)
    feedback_counts: Counter[str] = Counter(answer.feedback or "none" for answer in answers)
    audit_counts: Counter[str] = Counter(log.action for log in audit_logs)
    failed_documents = [
        {
            "id": document.id,
            "title": document.title,
            "filename": document.filename,
            "collection_id": document.collection_id,
            "error_message": document.error_message,
        }
        for document in documents
        if _status_value(document) == "failed"
    ][:20]
    recent_questions = [
        {
            "id": answer.id,
            "collection_id": answer.collection_id,
            "question": answer.question[:240],
            "evidence_score": answer.evidence_score,
            "feedback": answer.feedback,
            "created_at": answer.created_at.isoformat(),
        }
        for answer in answers[:20]
    ]
    return AdminMetricsOut(
        collection_count=len(collections),
        document_count=len(documents),
        document_status_counts=dict(status_counts),
        failed_documents=failed_documents,
        chunk_count=db.query(Chunk).count(),
        answer_count=len(answers),
        feedback_counts=dict(feedback_counts),
        low_evidence_answer_count=sum(1 for answer in answers if answer.evidence_score < 0.22),
        import_batch_count=db.query(ImportBatch).count(),
        audit_action_counts=dict(audit_counts),
        recent_questions=recent_questions,
    )


@router.get("/audit-logs", response_model=list[AuditLogOut])
def list_audit_logs(
    limit: int = 100,
    action: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> list[AuditLogOut]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(max(limit, 1), 500))
    if action:
        stmt = select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at.desc()).limit(min(max(limit, 1), 500))
    return [_audit_out(log) for log in db.scalars(stmt).all()]


@router.get("/knowledge-gaps", response_model=list[KnowledgeGapOut])
def list_knowledge_gaps(
    collection_id: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KnowledgeGapOut]:
    stmt = select(Answer).order_by(Answer.created_at.desc()).limit(min(max(limit, 1), 500))
    if collection_id:
        stmt = (
            select(Answer)
            .where(Answer.collection_id == collection_id)
            .order_by(Answer.created_at.desc())
            .limit(min(max(limit, 1), 500))
        )
    gaps = []
    for answer in db.scalars(stmt).all():
        collection = db.scalar(select(Collection).where(Collection.id == answer.collection_id))
        if not collection or not _can_access_collection(current_user, collection):
            continue
        if current_user.role not in {UserRole.admin, UserRole.maintainer} and answer.user_id != current_user.id:
            continue
        reason = _gap_reason(answer)
        if reason:
            gaps.append(_gap_out(answer, reason))
    return gaps


@router.post("/answers/{answer_id}/to-eval-case")
def answer_to_eval_case(
    answer_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> dict:
    answer = db.scalar(select(Answer).where(Answer.id == answer_id))
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")
    existing = db.scalar(select(EvalCase).where(EvalCase.collection_id == answer.collection_id, EvalCase.question == answer.question))
    if existing:
        return {"id": existing.id, "status": "exists"}
    case = EvalCase(
        collection_id=answer.collection_id,
        question=answer.question,
        expected_answer=None,
        expected_chunk_ids=[citation.get("chunk_id") for citation in answer.citations if citation.get("chunk_id")],
        metadata_={"source": "answer_gap", "answer_id": answer.id, "feedback": answer.feedback, "evidence_score": answer.evidence_score},
    )
    db.add(case)
    write_audit(db, current_user, "eval_case.from_answer_gap", "answer", answer.id, {"eval_case_id": case.id})
    db.commit()
    return {"id": case.id, "status": "created"}


def _build_ragflow_system_prompt() -> str:
    return (
        "You are an enterprise RAG knowledge-base assistant. "
        "Answer strictly from the retrieved evidence. "
        "Do not invent facts. "
        "Add citation markers after key conclusions using the format [1]. "
        "If evidence is insufficient, state exactly what is missing. "
        "Use the conversation history only to resolve follow-up references; "
        "do not treat history as factual evidence unless the retrieved evidence supports it."
    )


def _try_create_ragflow_chat(
    rag_client: RagFlowClient,
    dataset_id: str,
    collection_name: str,
    metadata: dict,
    payload_metadata: dict | None = None,
) -> None:
    """Create a RAGFlow chat/dialog when enabled, without blocking dataset creation."""
    settings = get_settings()
    if not settings.ragflow_enable_chat_completions:
        return
    try:
        rerank_id = (payload_metadata or {}).get("ragflow_reranker_model") or settings.ragflow_reranker_model
        chat = rag_client.create_chat(
            name=f"{collection_name}-chat",
            dataset_ids=[dataset_id],
            llm_id=settings.ragflow_chat_model,
            top_n=6,
            similarity_threshold=0.1,
            vector_similarity_weight=0.3,
            prompt_config={
                "system": _build_ragflow_system_prompt(),
                "quote": True,
                "refine_multiturn": True,
            },
            rerank_id=rerank_id or "",
        )
        metadata["ragflow_chat_id"] = chat.get("id")
        metadata["ragflow_chat_name"] = chat.get("name")
        metadata.pop("ragflow_chat_warning", None)
    except RagFlowError as exc:
        logger.warning("RAGFlow chat creation skipped for dataset %s: %s", dataset_id, exc)
        metadata["ragflow_chat_warning"] = str(exc)


@router.post("/collections", response_model=CollectionOut)
def create_collection(
    payload: CollectionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CollectionOut:
    existing = db.scalar(select(Collection).where(Collection.name == payload.name))
    if existing:
        raise HTTPException(status_code=409, detail="Collection name already exists")
    metadata = {
        **dict(payload.metadata or {}),
        "owner_id": current_user.id,
        "owner_username": current_user.email,
        "acl": [current_user.id],
    }
    if get_settings().ragflow_enabled:
        try:
            settings = get_settings()
            rag_client = RagFlowClient()
            dataset = rag_client.create_dataset(
                payload.name,
                payload.description,
                parser_config=payload.metadata.get("parser_config") if payload.metadata else None,
            )
            metadata["ragflow_dataset_id"] = dataset.get("id")
            metadata["ragflow_dataset_name"] = dataset.get("name")
            _try_create_ragflow_chat(
                rag_client,
                str(dataset.get("id")),
                payload.name,
                metadata,
                payload.metadata,
            )

            # Knowledge graph: if enabled in default settings or metadata
            if payload.metadata and payload.metadata.get("enable_knowledge_graph"):
                rag_client.enable_knowledge_graph(dataset.get("id"))
                metadata["ragflow_kg_enabled"] = True
        except RagFlowError as exc:
            raise HTTPException(status_code=502, detail=f"RAGFlow dataset creation failed: {exc}") from exc
    collection = Collection(name=payload.name, description=payload.description, metadata_=metadata)
    db.add(collection)
    write_audit(db, current_user, "collection.create", "collection", collection.id, {"name": payload.name})
    db.commit()
    return CollectionOut(id=collection.id, name=collection.name, description=collection.description, metadata=collection.metadata_)


@router.get("/collections", response_model=list[CollectionOut])
def list_collections(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CollectionOut]:
    collections = db.scalars(select(Collection).order_by(Collection.created_at.desc())).all()
    return [
        CollectionOut(id=item.id, name=item.name, description=item.description, metadata=item.metadata_ or {})
        for item in collections
        if _can_access_collection(current_user, item)
    ]


@router.delete("/collections/{collection_id}")
def delete_collection(
    collection_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection, write=True)

    metadata = collection.metadata_ or {}
    dataset_id = metadata.get("ragflow_dataset_id")
    if get_settings().ragflow_enabled and dataset_id:
        try:
            RagFlowClient().delete_dataset(str(dataset_id))
        except RagFlowError as exc:
            if not _ragflow_missing_resource(str(exc)):
                raise HTTPException(status_code=502, detail=f"RAGFlow dataset deletion failed: {exc}") from exc
            logger.warning("RAGFlow dataset already missing during collection delete: dataset=%s", dataset_id)

    from app.services.storage import ObjectStorage

    storage = ObjectStorage()
    documents = db.scalars(select(Document).where(Document.collection_id == collection_id)).all()
    for document in documents:
        try:
            storage.remove_object(document.object_key)
        except Exception:
            logger.warning("Object storage cleanup skipped for %s", document.object_key, exc_info=True)

    answers = db.scalars(select(Answer).where(Answer.collection_id == collection_id)).all()
    trace_ids = [answer.trace_id for answer in answers if answer.trace_id]
    db.query(Answer).filter(Answer.collection_id == collection_id).delete(synchronize_session=False)
    if trace_ids:
        db.query(RetrievalTrace).filter(RetrievalTrace.id.in_(trace_ids)).delete(synchronize_session=False)
    run_ids = [
        row[0]
        for row in db.query(EvaluationRun.id).filter(EvaluationRun.collection_id == collection_id).all()
    ]
    if run_ids:
        db.query(EvaluationResult).filter(EvaluationResult.run_id.in_(run_ids)).delete(synchronize_session=False)
    db.query(EvaluationRun).filter(EvaluationRun.collection_id == collection_id).delete(synchronize_session=False)
    db.query(EvaluationCase).filter(EvaluationCase.collection_id == collection_id).delete(synchronize_session=False)
    db.query(EvaluationDataset).filter(EvaluationDataset.collection_id == collection_id).delete(synchronize_session=False)
    db.query(EvalCase).filter(EvalCase.collection_id == collection_id).delete(synchronize_session=False)
    db.query(DomainLexiconTerm).filter(DomainLexiconTerm.collection_id == collection_id).delete(synchronize_session=False)
    db.query(RetrievalChunkCache).filter(RetrievalChunkCache.collection_id == collection_id).delete(synchronize_session=False)
    db.query(ImportBatch).filter(ImportBatch.collection_id == collection_id).delete(synchronize_session=False)
    db.query(RagflowSession).filter(RagflowSession.collection_id == collection_id).delete(synchronize_session=False)
    db.query(Chunk).filter(Chunk.collection_id == collection_id).delete(synchronize_session=False)
    db.query(Document).filter(Document.collection_id == collection_id).delete(synchronize_session=False)
    write_audit(db, current_user, "collection.delete", "collection", collection_id, {"name": collection.name, "ragflow_dataset_id": dataset_id})
    db.delete(collection)
    db.commit()
    return {"status": "deleted", "collection_id": collection_id}


@router.get("/collections/{collection_id}/sessions", response_model=list[ChatSessionOut])
def list_chat_sessions(
    collection_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatSessionOut]:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection)
    stmt = select(RagflowSession).where(RagflowSession.collection_id == collection_id)
    if current_user.role not in {UserRole.admin, UserRole.maintainer}:
        stmt = stmt.where(RagflowSession.user_id == current_user.id)
    sessions = db.scalars(stmt.order_by(RagflowSession.updated_at.desc())).all()
    return [_chat_session_out(session) for session in sessions]


@router.post("/collections/{collection_id}/sessions", response_model=ChatSessionOut)
def create_chat_session(
    collection_id: str,
    payload: ChatSessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatSessionOut:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection, write=True)
    session = _create_local_chat_session(db, collection, current_user, payload.title)
    write_audit(db, current_user, "chat_session.create", "collection", collection_id, {"session_id": session.session_id})
    db.commit()
    return _chat_session_out(session)


@router.delete("/collections/{collection_id}/sessions/{session_id}")
def delete_chat_session(
    collection_id: str,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    session = db.scalar(
        select(RagflowSession).where(
            RagflowSession.collection_id == collection_id,
            RagflowSession.session_id == session_id,
        )
    )
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    if session.user_id != current_user.id and current_user.role not in {UserRole.admin, UserRole.maintainer}:
        raise HTTPException(status_code=403, detail="No permission to delete this session")

    remote_session_id = (session.metadata_ or {}).get("ragflow_remote_session_id")
    if remote_session_id and session.chat_id:
        try:
            RagFlowClient().delete_sessions(session.chat_id, [str(remote_session_id)])
        except RagFlowError as exc:
            if not _ragflow_missing_resource(str(exc)):
                logger.warning("RAGFlow remote session deletion failed: %s", exc)

    answers = db.scalars(
        select(Answer).where(Answer.collection_id == collection_id, Answer.session_id == session_id)
    ).all()
    trace_ids = [answer.trace_id for answer in answers if answer.trace_id]
    db.query(Answer).filter(Answer.collection_id == collection_id, Answer.session_id == session_id).delete(synchronize_session=False)
    if trace_ids:
        db.query(RetrievalTrace).filter(RetrievalTrace.id.in_(trace_ids)).delete(synchronize_session=False)
    write_audit(db, current_user, "chat_session.delete", "collection", collection_id, {"session_id": session_id})
    db.delete(session)
    db.commit()
    return {"status": "deleted", "session_id": session_id}


@router.post("/collections/{collection_id}/documents", response_model=DocumentOut)
async def upload_document(
    collection_id: str,
    file: UploadFile = File(...),
    author: str | None = Form(default=None),
    project: str | None = Form(default=None),
    meeting_date: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    acl: str | None = Form(default=None),
    source_uri: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer, UserRole.member)),
) -> DocumentOut:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection, write=True)
    filename = _safe_upload_filename(file.filename, "document")
    data = await _read_upload_bytes(file, get_settings().max_upload_size_mb)
    checksum = sha256_bytes(data)
    existing = db.scalar(select(Document).where(Document.collection_id == collection_id, Document.checksum == checksum))
    if existing:
        return _document_out(existing)
    object_key = f"{collection_id}/{uuid.uuid4()}-{filename}"
    from app.services.storage import ObjectStorage

    ObjectStorage().put_bytes(object_key, data, file.content_type)
    document = Document(
        collection_id=collection_id,
        title=filename,
        filename=filename,
        content_type=file.content_type,
        object_key=object_key,
        checksum=checksum,
        source_uri=source_uri,
        author=author,
        project=project,
        meeting_date=meeting_date,
        tags=[item.strip() for item in (tags or "").split(",") if item.strip()],
        acl=[item.strip() for item in (acl or current_user.id).split(",") if item.strip()],
        metadata_={"ingest_source": "upload", "original_filename": file.filename or filename},
    )
    db.add(document)
    write_audit(
        db,
        current_user,
        "document.upload",
        "document",
        document.id,
        {"filename": document.filename, "collection_id": collection_id, "project": project},
    )
    db.commit()
    _ingest_with_ragflow_or_local(db, collection, document, data, file.content_type)
    return _document_out(document)


@router.post("/collections/{collection_id}/url-documents", response_model=DocumentOut)
async def ingest_url_document(
    collection_id: str,
    payload: UrlIngestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer, UserRole.member)),
) -> DocumentOut:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection, write=True)
    from app.services.web_ingest import WebIngestionError, fetch_web_document

    try:
        filename, data, content_type = await fetch_web_document(payload.url)
    except WebIngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {exc}") from exc

    checksum = sha256_bytes(data)
    existing = db.scalar(select(Document).where(Document.collection_id == collection_id, Document.checksum == checksum))
    if existing:
        return _document_out(existing)
    object_key = f"{collection_id}/{uuid.uuid4()}-{filename}"
    from app.services.storage import ObjectStorage

    ObjectStorage().put_bytes(object_key, data, content_type)
    document = Document(
        collection_id=collection_id,
        title=filename.removesuffix(".html"),
        filename=filename,
        content_type=content_type,
        object_key=object_key,
        checksum=checksum,
        source_uri=payload.url,
        author=payload.author,
        project=payload.project,
        meeting_date=payload.meeting_date,
        tags=payload.tags,
        acl=payload.acl or [current_user.id],
        metadata_={"ingest_source": "url"},
    )
    db.add(document)
    write_audit(
        db,
        current_user,
        "document.ingest_url",
        "document",
        document.id,
        {"url": payload.url, "collection_id": collection_id, "project": payload.project},
    )
    db.commit()
    _ingest_with_ragflow_or_local(db, collection, document, data, content_type)
    return _document_out(document)


@router.post("/collections/{collection_id}/batch-zip", response_model=ImportBatchOut)
async def ingest_zip_batch(
    collection_id: str,
    file: UploadFile = File(...),
    author: str | None = Form(default=None),
    project: str | None = Form(default=None),
    meeting_date: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    acl: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer, UserRole.member)),
) -> ImportBatchOut:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection, write=True)
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip batch imports are supported")
    archive_filename = _safe_upload_filename(file.filename, "batch.zip")
    archive_data = await _read_upload_bytes(file, get_settings().max_zip_upload_size_mb)
    result = read_zip_documents(archive_data)
    batch_report = {"skipped": result.skipped, "failed": result.failed, "documents": []}
    batch = ImportBatch(
        collection_id=collection_id,
        source_type="zip",
        source_name=archive_filename,
        total_items=len(result.entries) + len(result.skipped) + len(result.failed),
        skipped_items=len(result.skipped),
        failed_items=len(result.failed),
        created_by=current_user.id,
        report=batch_report,
    )
    db.add(batch)
    db.flush()

    from app.services.storage import ObjectStorage

    storage = ObjectStorage()
    imported = 0
    duplicate_skips = []
    parsed_tags = [item.strip() for item in (tags or "").split(",") if item.strip()]
    parsed_acl = [item.strip() for item in (acl or current_user.id).split(",") if item.strip()]
    pending_ingestions: list[tuple[Document, bytes, str | None]] = []
    for entry in result.entries:
        entry_filename = _safe_upload_filename(entry.filename, "document")
        checksum = sha256_bytes(entry.data)
        existing = db.scalar(select(Document).where(Document.collection_id == collection_id, Document.checksum == checksum))
        if existing:
            duplicate_skips.append({"filename": entry_filename, "reason": "duplicate", "document_id": existing.id})
            continue
        object_key = f"{collection_id}/{uuid.uuid4()}-{entry_filename}"
        storage.put_bytes(object_key, entry.data, entry.content_type)
        document = Document(
            collection_id=collection_id,
            title=entry_filename,
            filename=entry_filename,
            content_type=entry.content_type,
            object_key=object_key,
            checksum=checksum,
            source_uri=f"zip://{archive_filename}/{entry_filename}",
            author=author,
            project=project,
            meeting_date=meeting_date,
            tags=parsed_tags,
            acl=parsed_acl,
            metadata_={"ingest_source": "zip", "import_batch_id": batch.id, "original_filename": entry.filename},
        )
        db.add(document)
        db.flush()
        batch_report["documents"].append({"filename": entry_filename, "document_id": document.id})
        imported += 1
        pending_ingestions.append((document, entry.data, entry.content_type))

    batch.imported_items = imported
    batch.skipped_items += len(duplicate_skips)
    batch_report["skipped"].extend(duplicate_skips)
    batch.report = batch_report
    write_audit(
        db,
        current_user,
        "batch.ingest_zip",
        "import_batch",
        batch.id,
        {"collection_id": collection_id, "source_name": batch.source_name, "imported_items": imported},
    )
    db.commit()
    for document, data, content_type in pending_ingestions:
        _ingest_with_ragflow_or_local(db, collection, document, data, content_type)
    return _batch_out(batch)


@router.get("/collections/{collection_id}/documents", response_model=list[DocumentOut])
def list_documents(
    collection_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DocumentOut]:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection)
    if get_settings().ragflow_enabled:
        _sync_ragflow_document_statuses(db, collection)
    documents = db.scalars(select(Document).where(Document.collection_id == collection_id).order_by(Document.created_at.desc())).all()
    return [_document_out(document) for document in documents if can_read_acl(current_user, document.acl or [])]


@router.get("/collections/{collection_id}/answers", response_model=list[AnswerOut])
def list_collection_answers(
    collection_id: str,
    limit: int = 500,
    session_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AnswerOut]:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection)
    stmt = select(Answer).where(Answer.collection_id == collection_id)
    if session_id:
        stmt = stmt.where(Answer.session_id == session_id)
    if current_user.role not in {UserRole.admin, UserRole.maintainer}:
        stmt = stmt.where(Answer.user_id == current_user.id)
    answers = db.scalars(stmt.order_by(Answer.created_at.desc()).limit(min(max(limit, 1), 1000))).all()
    return [_answer_out(answer) for answer in reversed(answers)]


@router.get("/collections/{collection_id}/import-batches", response_model=list[ImportBatchOut])
def list_import_batches(
    collection_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ImportBatchOut]:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection)
    batches = db.scalars(
        select(ImportBatch).where(ImportBatch.collection_id == collection_id).order_by(ImportBatch.created_at.desc())
    ).all()
    return [_batch_out(batch) for batch in batches]


@router.get("/collections/{collection_id}/quality", response_model=CollectionQualityOut)
def collection_quality(
    collection_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CollectionQualityOut:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection)
    if get_settings().ragflow_enabled:
        _sync_ragflow_document_statuses(db, collection)
    documents = db.scalars(select(Document).where(Document.collection_id == collection_id)).all()
    warning_counts: Counter[str] = Counter()
    term_counts: Counter[str] = Counter()
    ready_count = 0
    failed_count = 0
    total_chunks = 0
    quality_scores = []
    parser_counts: Counter[str] = Counter()
    for document in documents:
        status = _status_value(document)
        if status == "ready":
            ready_count += 1
        if status == "failed":
            failed_count += 1
        analysis = (document.metadata_ or {}).get("analysis", {})
        parsed_quality = (document.metadata_ or {}).get("parsed_quality") or (document.metadata_ or {}).get("raw_quality") or {}
        if parsed_quality.get("score") is not None:
            quality_scores.append(float(parsed_quality.get("score") or 0))
        parser_counts[str((document.metadata_ or {}).get("parser_engine") or "unknown")] += 1
        total_chunks += int(analysis.get("chunk_count", 0) or 0)
        warning_counts.update(analysis.get("quality_warnings", []))
        warning_counts.update(parsed_quality.get("warnings", []))
        for term, count in analysis.get("top_terms", []):
            term_counts[str(term)] += int(count)
    return CollectionQualityOut(
        collection_id=collection_id,
        document_count=len(documents),
        ready_count=ready_count,
        failed_count=failed_count,
        total_chunks=total_chunks,
        avg_chunks_per_ready_document=round(total_chunks / ready_count, 2) if ready_count else 0.0,
        warning_counts=dict(warning_counts),
        top_terms=term_counts.most_common(30),
        avg_parse_quality_score=round(sum(quality_scores) / len(quality_scores), 3) if quality_scores else 0.0,
        parser_engine_counts=dict(parser_counts),
    )


@router.get("/collections/{collection_id}/meeting-summary", response_model=MeetingSummaryOut)
def meeting_summary(
    collection_id: str,
    meeting_date: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MeetingSummaryOut:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection)
    stmt = select(Document).where(Document.collection_id == collection_id)
    if meeting_date:
        stmt = stmt.where(Document.meeting_date == meeting_date)
    documents = [document for document in db.scalars(stmt).all() if can_read_acl(current_user, document.acl or [])]
    authors: set[str] = set()
    projects: set[str] = set()
    progress: list[str] = []
    risks: list[str] = []
    next_steps: list[str] = []
    decisions: list[str] = []
    key_points: list[str] = []
    for document in documents:
        if document.author:
            authors.add(document.author)
        if document.project:
            projects.add(document.project)
        lab_signals = ((document.metadata_ or {}).get("analysis", {}).get("lab_signals", {}))
        progress.extend(_prefix_items(document, lab_signals.get("progress", [])))
        risks.extend(_prefix_items(document, lab_signals.get("risks", [])))
        next_steps.extend(_prefix_items(document, lab_signals.get("next_steps", [])))
        decisions.extend(_prefix_items(document, lab_signals.get("decisions", [])))
        key_points.extend(_prefix_items(document, lab_signals.get("key_points", [])))
    return MeetingSummaryOut(
        collection_id=collection_id,
        meeting_date=meeting_date,
        document_count=len(documents),
        authors=sorted(authors),
        projects=sorted(projects),
        progress=_dedupe(progress)[:50],
        risks=_dedupe(risks)[:50],
        next_steps=_dedupe(next_steps)[:50],
        decisions=_dedupe(decisions)[:50],
        key_points=_dedupe(key_points)[:50],
    )


@router.get("/documents/{document_id}/analysis", response_model=DocumentAnalysisOut)
def document_analysis(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentAnalysisOut:
    document = db.scalar(select(Document).where(Document.id == document_id))
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    collection = db.scalar(select(Collection).where(Collection.id == document.collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection)
    if not can_read_acl(current_user, document.acl or []):
        raise HTTPException(status_code=403, detail="No permission to read this document")
    return DocumentAnalysisOut(
        document=_document_out(document),
        analysis=(document.metadata_ or {}).get("analysis", {}),
        metadata=document.metadata_ or {},
    )


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    document = db.scalar(select(Document).where(Document.id == document_id))
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    collection = db.scalar(select(Collection).where(Collection.id == document.collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection, write=True)
    if collection and get_settings().ragflow_enabled:
        dataset_id = (collection.metadata_ or {}).get("ragflow_dataset_id")
        ragflow_doc_id = (document.metadata_ or {}).get("ragflow_document_id")
        if dataset_id and ragflow_doc_id:
            try:
                RagFlowClient().delete_document(dataset_id, ragflow_doc_id)
            except RagFlowError as exc:
                if not _ragflow_missing_resource(str(exc)):
                    raise HTTPException(status_code=502, detail=f"RAGFlow document deletion failed: {exc}") from exc
                logger.warning("RAGFlow document already missing during delete: dataset=%s document=%s", dataset_id, ragflow_doc_id)
    from app.services.storage import ObjectStorage

    if not get_settings().ragflow_enabled:
        from app.rag.vector_store import VectorStore

        VectorStore().delete_document(document.id)
    try:
        ObjectStorage().remove_object(document.object_key)
    except Exception:
        pass
    db.query(Chunk).filter(Chunk.document_id == document.id).delete()
    db.query(RetrievalChunkCache).filter(RetrievalChunkCache.document_id == document.id).delete()
    write_audit(
        db,
        current_user,
        "document.delete",
        "document",
        document.id,
        {"collection_id": document.collection_id, "filename": document.filename},
    )
    db.delete(document)
    db.commit()
    return {"status": "deleted", "document_id": document_id}


@router.post("/documents/{document_id}/reindex", response_model=DocumentOut)
def reindex_document(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentOut:
    document = db.scalar(select(Document).where(Document.id == document_id))
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    collection = db.scalar(select(Collection).where(Collection.id == document.collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection, write=True)
    write_audit(db, current_user, "document.reindex", "document", document.id, {"collection_id": document.collection_id})
    db.commit()
    if get_settings().ragflow_enabled:
        dataset_id = (collection.metadata_ or {}).get("ragflow_dataset_id") if collection else None
        ragflow_doc_id = (document.metadata_ or {}).get("ragflow_document_id") if document.metadata_ else None
        if dataset_id and ragflow_doc_id:
            client = RagFlowClient()
            if not _ragflow_document_belongs_to_dataset(client, dataset_id, ragflow_doc_id):
                raise HTTPException(status_code=409, detail="RAGFlow 文档不属于当前知识库，请删除后重新上传该文件")
            client.parse_documents(dataset_id, [ragflow_doc_id])
            document.status = DocumentStatus.parsing
            db.commit()
    else:
        _schedule_ingestion(document.id)
    return _document_out(document)


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatResponse:
    payload.user_id = current_user.id
    collection = db.scalar(select(Collection).where(Collection.id == payload.collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection)
    session = _ensure_local_chat_session(db, collection, current_user, payload.session_id)
    payload.session_id = session.session_id
    payload.user_acl_principals = [current_user.id, current_user.email, current_user.role.value, "public"]
    write_audit(db, current_user, "chat.query", "collection", payload.collection_id, {"question_preview": payload.question[:160]})
    db.commit()
    from app.services.chat import ChatService

    try:
        return ChatService(db).ask(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """SSE-streaming chat endpoint.  Yields partial answer text as it arrives
    from RAGFlow, then sends the final event with complete answer + citations."""
    payload.user_id = current_user.id
    collection = db.scalar(select(Collection).where(Collection.id == payload.collection_id))
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    _require_collection_access(current_user, collection)
    session = _ensure_local_chat_session(db, collection, current_user, payload.session_id)
    payload.session_id = session.session_id
    payload.user_acl_principals = [current_user.id, current_user.email, current_user.role.value, "public"]
    write_audit(db, current_user, "chat.stream", "collection", payload.collection_id, {"question_preview": payload.question[:160]})
    db.commit()

    from app.services.chat import ChatService

    async def event_generator():
        from app.core.db import SessionLocal

        db_local = SessionLocal()
        try:
            for event in ChatService(db_local).ask_stream(payload):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            db_local.close()
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/answers/{answer_id}/feedback")
def set_feedback(
    answer_id: str,
    payload: FeedbackIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    answer = db.scalar(select(Answer).where(Answer.id == answer_id))
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")
    collection = db.scalar(select(Collection).where(Collection.id == answer.collection_id))
    if not collection or not _can_access_collection(current_user, collection):
        raise HTTPException(status_code=403, detail="No permission to update this answer")
    if current_user.role not in {UserRole.admin, UserRole.maintainer} and answer.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to update this answer")
    answer.feedback = payload.feedback
    write_audit(db, current_user, "answer.feedback", "answer", answer.id, {"feedback": payload.feedback})
    db.commit()
    return {"status": "ok"}


@router.post("/eval/cases")
def create_eval_case(
    payload: EvalCaseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> dict:
    case = EvalCase(
        collection_id=payload.collection_id,
        question=payload.question,
        expected_answer=payload.expected_answer,
        expected_chunk_ids=payload.expected_chunk_ids,
        metadata_=payload.metadata,
    )
    db.add(case)
    write_audit(db, current_user, "eval_case.create", "eval_case", case.id, {"collection_id": payload.collection_id})
    db.commit()
    return {"id": case.id}


@router.post("/eval/{collection_id}/retrieval")
def run_retrieval_eval(
    collection_id: str,
    strategy: RetrievalStrategy,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> dict:
    write_audit(db, current_user, "eval.run_retrieval", "collection", collection_id, {"strategy": strategy.model_dump()})
    db.commit()
    return EvaluationService(db).run_retrieval_eval(collection_id, strategy)


@router.post("/eval/{collection_id}/compare", response_model=StrategyCompareOut)
def compare_strategies(
    collection_id: str,
    payload: StrategyCompareIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> StrategyCompareOut:
    strategies = {name: RetrievalStrategy(**config) for name, config in payload.strategies.items()}
    write_audit(
        db,
        current_user,
        "eval.compare_strategies",
        "collection",
        collection_id,
        {"strategy_names": list(strategies.keys())},
    )
    db.commit()
    result = EvaluationService(db).compare_strategies(collection_id, strategies)
    return StrategyCompareOut(**result)


@router.get("/evaluation/datasets")
def list_evaluation_datasets(
    collection_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection or not _can_access_collection(current_user, collection):
        raise HTTPException(status_code=404, detail="Collection not found")
    datasets = db.scalars(
        select(EvaluationDataset)
        .where(EvaluationDataset.collection_id == collection_id)
        .order_by(EvaluationDataset.created_at.desc())
    ).all()
    return {"items": [_evaluation_dataset_out(db, dataset) for dataset in datasets]}


@router.post("/evaluation/datasets")
def create_evaluation_dataset(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> dict:
    collection_id = str(payload.get("collection_id") or "")
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    if not collection or not _can_access_collection(current_user, collection):
        raise HTTPException(status_code=404, detail="Collection not found")
    dataset = EvaluationDataset(
        collection_id=collection_id,
        name=str(payload.get("name") or "默认测评集")[:200],
        description=payload.get("description"),
        metadata_=payload.get("metadata") or {},
        created_by=current_user.id,
    )
    db.add(dataset)
    write_audit(db, current_user, "evaluation_dataset.create", "collection", collection_id, {"name": dataset.name})
    db.commit()
    db.refresh(dataset)
    return _evaluation_dataset_out(db, dataset)


@router.post("/evaluation/datasets/{dataset_id}/cases")
def create_evaluation_case(
    dataset_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> dict:
    dataset = db.scalar(select(EvaluationDataset).where(EvaluationDataset.id == dataset_id))
    if not dataset:
        raise HTTPException(status_code=404, detail="Evaluation dataset not found")
    collection = db.scalar(select(Collection).where(Collection.id == dataset.collection_id))
    if not collection or not _can_access_collection(current_user, collection):
        raise HTTPException(status_code=404, detail="Collection not found")
    case = EvaluationCase(
        dataset_id=dataset.id,
        collection_id=dataset.collection_id,
        question=str(payload.get("question") or "").strip(),
        expected_answer=payload.get("expected_answer"),
        expected_chunk_ids=payload.get("expected_chunk_ids") or [],
        tags=payload.get("tags") or [],
        metadata_=payload.get("metadata") or {},
    )
    if not case.question:
        raise HTTPException(status_code=422, detail="Question is required")
    db.add(case)
    write_audit(db, current_user, "evaluation_case.create", "evaluation_dataset", dataset_id, {"question": case.question})
    db.commit()
    return _evaluation_case_out(case)


@router.post("/evaluation/datasets/{dataset_id}/runs")
def run_evaluation_dataset(
    dataset_id: str,
    payload: dict | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> dict:
    dataset = db.scalar(select(EvaluationDataset).where(EvaluationDataset.id == dataset_id))
    if not dataset:
        raise HTTPException(status_code=404, detail="Evaluation dataset not found")
    collection = db.scalar(select(Collection).where(Collection.id == dataset.collection_id))
    if not collection or not _can_access_collection(current_user, collection):
        raise HTTPException(status_code=404, detail="Collection not found")
    run = EvaluationRun(
        dataset_id=dataset.id,
        collection_id=dataset.collection_id,
        strategy=(payload or {}).get("strategy") or {"retriever": "local_bm25_cache", "top_k": 10},
        status="running",
        created_by=current_user.id,
    )
    db.add(run)
    db.flush()
    cases = db.scalars(
        select(EvaluationCase)
        .where(EvaluationCase.dataset_id == dataset.id)
        .order_by(EvaluationCase.created_at.asc())
    ).all()
    totals = {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0, "citation_readability": 0.0}
    failures: list[dict] = []
    for case in cases:
        try:
            chunks = local_bm25_search(
                db,
                collection_id=dataset.collection_id,
                query=case.question,
                top_k=int(run.strategy.get("top_k") or 10),
                acl_principals=[current_user.id, "public"],
            )
            citations = [_evaluation_citation_from_chunk(chunk) for chunk in chunks]
            retrieved_ids = [chunk.chunk_id for chunk in chunks]
            expected_ids = case.expected_chunk_ids or []
            metrics = retrieval_metrics(retrieved_ids, expected_ids, k=int(run.strategy.get("top_k") or 10))
            readability = citation_readability_rate(citations)
            case_metrics = {
                "recall_at_k": metrics.recall_at_k,
                "precision_at_k": metrics.precision_at_k,
                "mrr": metrics.mrr,
                "ndcg_at_k": metrics.ndcg_at_k,
                "hit": metrics.hit,
                "faithfulness": readability if citations else 0.0,
                "citation_readability": readability,
            }
            passed = bool(metrics.hit) if expected_ids else bool(citations and readability >= 0.8)
            if not passed:
                failures.append({"case_id": case.id, "question": case.question, "metrics": case_metrics})
            db.add(
                EvaluationResult(
                    run_id=run.id,
                    case_id=case.id,
                    question=case.question,
                    answer=None,
                    retrieved_chunk_ids=retrieved_ids,
                    expected_chunk_ids=expected_ids,
                    metrics=case_metrics,
                    citations=citations,
                    passed=passed,
                )
            )
            totals["recall"] += metrics.recall_at_k
            totals["mrr"] += metrics.mrr
            totals["ndcg"] += metrics.ndcg_at_k
            totals["citation_readability"] += readability
        except Exception as exc:
            failures.append({"case_id": case.id, "question": case.question, "error": str(exc)})
            db.add(
                EvaluationResult(
                    run_id=run.id,
                    case_id=case.id,
                    question=case.question,
                    metrics={},
                    error_message=str(exc),
                    passed=False,
                )
            )
    total = len(cases)
    run.status = "completed"
    run.completed_at = datetime.utcnow()
    run.metrics = {
        "case_count": total,
        "recall_at_k": totals["recall"] / total if total else 0.0,
        "mrr": totals["mrr"] / total if total else 0.0,
        "ndcg_at_k": totals["ndcg"] / total if total else 0.0,
        "faithfulness": totals["citation_readability"] / total if total else 0.0,
        "citation_readability": totals["citation_readability"] / total if total else 0.0,
        "failure_count": len(failures),
        "failures": failures[:20],
    }
    write_audit(db, current_user, "evaluation_run.create", "evaluation_dataset", dataset_id, run.metrics)
    db.commit()
    db.refresh(run)
    return _evaluation_run_out(db, run)


@router.get("/evaluation/runs/{run_id}")
def get_evaluation_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    run = db.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id))
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    collection = db.scalar(select(Collection).where(Collection.id == run.collection_id))
    if not collection or not _can_access_collection(current_user, collection):
        raise HTTPException(status_code=404, detail="Collection not found")
    return _evaluation_run_out(db, run)


@router.post("/users", response_model=UserOut)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin)),
) -> UserOut:
    username = _normalize_username(payload.username)
    existing = db.scalar(select(User).where(User.email == username))
    if existing:
        raise HTTPException(status_code=409, detail="User username already exists")
    user = User(
        email=username,
        name=(payload.name or username).strip() or username,
        role=UserRole(payload.role),
        api_key_hash=hash_api_key(payload.api_key),
    )
    db.add(user)
    write_audit(db, current_user, "user.create", "user", user.id, {"username": user.email, "role": user.role.value})
    db.commit()
    return _user_out(user)


@router.get("/users/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(current_user)


@router.post("/strategies", response_model=StrategyPresetOut)
def create_strategy(
    payload: StrategyPresetCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.maintainer)),
) -> StrategyPresetOut:
    existing = db.scalar(select(RagStrategyPreset).where(RagStrategyPreset.name == payload.name))
    if existing:
        raise HTTPException(status_code=409, detail="Strategy name already exists")
    if payload.is_default:
        for preset in db.scalars(select(RagStrategyPreset).where(RagStrategyPreset.is_default.is_(True))).all():
            preset.is_default = False
    preset = RagStrategyPreset(
        name=payload.name,
        description=payload.description,
        config=payload.config,
        is_default=payload.is_default,
        created_by=current_user.id,
    )
    db.add(preset)
    write_audit(db, current_user, "strategy.create", "strategy", preset.id, {"name": preset.name})
    db.commit()
    return _strategy_out(preset)


@router.get("/strategies", response_model=list[StrategyPresetOut])
def list_strategies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[StrategyPresetOut]:
    presets = db.scalars(select(RagStrategyPreset).order_by(RagStrategyPreset.created_at.desc())).all()
    return [_strategy_out(preset) for preset in presets]


@router.get("/traces/{trace_id}")
def get_trace(
    trace_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    trace = db.scalar(select(RetrievalTrace).where(RetrievalTrace.id == trace_id))
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    collection = db.scalar(select(Collection).where(Collection.id == trace.collection_id))
    if not collection or not _can_access_collection(current_user, collection):
        raise HTTPException(status_code=403, detail="No permission to read this trace")
    return {
        "id": trace.id,
        "collection_id": trace.collection_id,
        "query": trace.query,
        "rewritten_query": trace.rewritten_query,
        "strategy": trace.strategy,
        "candidates": trace.candidates,
        "selected_context": trace.selected_context,
        "created_at": trace.created_at.isoformat(),
    }


async def _read_upload_bytes(file: UploadFile, max_mb: int) -> bytes:
    max_bytes = max_mb * 1024 * 1024
    data = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail=f"文件超过大小限制：最大 {max_mb} MB")
    return bytes(data)


def _safe_upload_filename(filename: str | None, fallback: str) -> str:
    name = PurePath(filename or fallback).name
    name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "-", name).strip(" ._-")
    if not name:
        name = fallback
    if "." not in name and "." in fallback:
        name = f"{name}{PurePath(fallback).suffix}"
    return name[:180]


def _ragflow_missing_resource(message: str) -> bool:
    lowered = message.lower()
    return any(token in lowered for token in ("not found", "does not exist", "doesn't exist", "404"))


def _ragflow_document_belongs_to_dataset(client: RagFlowClient, dataset_id: str, document_id: str) -> bool:
    try:
        documents = client.list_documents(str(dataset_id))
    except RagFlowError:
        return False
    return any(str(item.get("id") or "") == str(document_id) for item in documents)


def _create_local_chat_session(
    db: Session,
    collection: Collection,
    current_user: User,
    title: str | None = None,
    session_id: str | None = None,
) -> RagflowSession:
    safe_session_id = _chat_session_id(session_id)
    session = RagflowSession(
        collection_id=collection.id,
        chat_id=str((collection.metadata_ or {}).get("ragflow_chat_id") or ""),
        session_id=safe_session_id,
        user_id=current_user.id,
        turn_count=0,
        title=(title or "新对话").strip()[:500],
        metadata_={"kind": "management_chat_session"},
    )
    db.add(session)
    db.flush()
    return session


def _ensure_local_chat_session(
    db: Session,
    collection: Collection,
    current_user: User,
    session_id: str | None,
) -> RagflowSession:
    safe_session_id = _chat_session_id(session_id)
    session = db.scalar(
        select(RagflowSession).where(
            RagflowSession.collection_id == collection.id,
            RagflowSession.session_id == safe_session_id,
        )
    )
    if session:
        if session.user_id != current_user.id and current_user.role not in {UserRole.admin, UserRole.maintainer}:
            raise HTTPException(status_code=403, detail="No permission to use this chat session")
        return session
    return _create_local_chat_session(db, collection, current_user, title="新对话", session_id=safe_session_id)


def _chat_session_out(session: RagflowSession) -> ChatSessionOut:
    return ChatSessionOut(
        session_id=session.session_id,
        collection_id=session.collection_id,
        title=session.title,
        turn_count=session.turn_count,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
    )


def _document_out(document: Document) -> DocumentOut:
    analysis = (document.metadata_ or {}).get("analysis") or {}
    parsed_quality = (document.metadata_ or {}).get("parsed_quality") or (document.metadata_ or {}).get("raw_quality") or {}
    return DocumentOut(
        id=document.id,
        collection_id=document.collection_id,
        title=document.title,
        filename=document.filename,
        status=_status_value(document),
        error_message=document.error_message,
        author=document.author,
        project=document.project,
        meeting_date=document.meeting_date,
        tags=document.tags or [],
        status_message=_document_status_message(document, analysis),
        ragflow_progress=float(analysis.get("progress") or 0),
        chunk_count=int(analysis.get("chunk_count") or 0),
        token_count=int(analysis.get("token_count") or 0),
        parser_engine=(document.metadata_ or {}).get("parser_engine"),
        parse_quality_score=parsed_quality.get("score"),
        parse_quality_warnings=parsed_quality.get("warnings") or [],
    )


def _answer_out(answer: Answer) -> AnswerOut:
    return AnswerOut(
        id=answer.id,
        trace_id=answer.trace_id,
        collection_id=answer.collection_id,
        user_id=answer.user_id,
        session_id=answer.session_id,
        question=answer.question,
        answer=answer.answer,
        citations=answer.citations or [],
        model=answer.model,
        evidence_score=answer.evidence_score,
        feedback=answer.feedback,
        created_at=answer.created_at.isoformat(),
    )


def _evaluation_dataset_out(db: Session, dataset: EvaluationDataset) -> dict:
    case_count = db.scalar(
        select(func.count()).select_from(EvaluationCase).where(EvaluationCase.dataset_id == dataset.id)
    )
    return {
        "id": dataset.id,
        "collection_id": dataset.collection_id,
        "name": dataset.name,
        "description": dataset.description,
        "metadata": dataset.metadata_ or {},
        "case_count": int(case_count or 0),
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
    }


def _evaluation_case_out(case: EvaluationCase) -> dict:
    return {
        "id": case.id,
        "dataset_id": case.dataset_id,
        "collection_id": case.collection_id,
        "question": case.question,
        "expected_answer": case.expected_answer,
        "expected_chunk_ids": case.expected_chunk_ids or [],
        "tags": case.tags or [],
        "metadata": case.metadata_ or {},
        "created_at": case.created_at.isoformat() if case.created_at else None,
    }


def _evaluation_run_out(db: Session, run: EvaluationRun) -> dict:
    results = db.scalars(
        select(EvaluationResult)
        .where(EvaluationResult.run_id == run.id)
        .order_by(EvaluationResult.created_at.asc())
    ).all()
    return {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "collection_id": run.collection_id,
        "strategy": run.strategy or {},
        "status": run.status,
        "metrics": run.metrics or {},
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "results": [
            {
                "id": item.id,
                "case_id": item.case_id,
                "question": item.question,
                "retrieved_chunk_ids": item.retrieved_chunk_ids or [],
                "expected_chunk_ids": item.expected_chunk_ids or [],
                "metrics": item.metrics or {},
                "citations": item.citations or [],
                "passed": item.passed,
                "error_message": item.error_message,
            }
            for item in results
        ],
    }


def _evaluation_citation_from_chunk(chunk) -> dict:
    metadata = chunk.metadata or {}
    cache = metadata.get("local_cache") or {}
    content = chunk.content or ""
    return {
        "chunk_id": chunk.chunk_id,
        "document_id": chunk.document_id,
        "title": chunk.title,
        "title_path": chunk.title_path or [],
        "score": chunk.score,
        "preview": " ".join(content.split())[:520],
        "content": content,
        "page": cache.get("page") or metadata.get("page"),
        "metadata": metadata,
    }


def _chat_session_id(value: str | None) -> str:
    text = (value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9._:-]{8,80}", text):
        return text
    return str(uuid.uuid4())


def _status_value(document: Document) -> str:
    return document.status.value if hasattr(document.status, "value") else str(document.status)


def _document_status_message(document: Document, analysis: dict) -> str:
    status = _status_value(document)
    if status == "ready":
        chunks = int(analysis.get("chunk_count") or 0)
        tokens = int(analysis.get("token_count") or 0)
        quality = ((document.metadata_ or {}).get("parsed_quality") or (document.metadata_ or {}).get("raw_quality") or {}).get("score")
        quality_suffix = f"，解析质量 {quality}" if quality is not None else ""
        if chunks:
            return f"RAGFlow 已解析完成，生成 {chunks} 个分块，并写入向量索引{quality_suffix}。"
        if tokens:
            return f"RAGFlow 已解析完成，写入向量索引，约 {tokens} tokens{quality_suffix}。"
        return f"RAGFlow 已解析完成并写入向量索引{quality_suffix}。"
    if status == "parsing":
        progress = float(analysis.get("progress") or 0)
        progress_msg = analysis.get("progress_msg") or "RAGFlow 正在解析、分块和构建索引。"
        return f"{progress_msg}（{round(progress * 100)}%）"
    if status == "failed":
        return document.error_message or "RAGFlow 解析失败，请查看日志。"
    return "文档已进入 RAGFlow 处理队列。"


def _ragflow_status_quality_warnings(remote: dict) -> list[str]:
    warnings: list[str] = []
    chunk_count = int(remote.get("chunk_count") or 0)
    token_count = int(remote.get("token_count") or 0)
    progress = float(remote.get("progress") or 0)
    if progress >= 1.0 and chunk_count == 0:
        warnings.append("ragflow_zero_chunks")
    if progress >= 1.0 and token_count == 0:
        warnings.append("ragflow_zero_tokens")
    progress_msg = str(remote.get("progress_msg") or "").lower()
    if "error" in progress_msg or "fail" in progress_msg:
        warnings.append("ragflow_progress_warning")
    return warnings


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.email,
        email=user.email,
        name=user.name,
        role=user.role.value,
        is_active=user.is_active,
    )


def _token_pair(user: User) -> TokenPairOut:
    settings = get_settings()
    version = int(user.token_version or 1)
    return TokenPairOut(
        access_token=create_access_token(user.id, user.role.value, version),
        refresh_token=create_refresh_token(user.id, version),
        expires_in=settings.access_token_minutes * 60,
        user=_user_out(user),
    )


def _model_config_out(model: ModelConfig) -> ModelConfigOut:
    return ModelConfigOut(
        id=model.id,
        name=model.name,
        provider=model.provider,
        model_name=model.model_name,
        base_url=model.base_url,
        api_key_masked=_mask_secret(model.api_key),
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        active=model.active,
    )


def _strategy_out(preset: RagStrategyPreset) -> StrategyPresetOut:
    return StrategyPresetOut(
        id=preset.id,
        name=preset.name,
        description=preset.description,
        config=preset.config or {},
        is_default=preset.is_default,
    )


def _batch_out(batch: ImportBatch) -> ImportBatchOut:
    return ImportBatchOut(
        id=batch.id,
        collection_id=batch.collection_id,
        source_type=batch.source_type,
        source_name=batch.source_name,
        status=batch.status,
        total_items=batch.total_items,
        imported_items=batch.imported_items,
        skipped_items=batch.skipped_items,
        failed_items=batch.failed_items,
        report=batch.report or {},
    )


def _audit_out(log: AuditLog) -> AuditLogOut:
    return AuditLogOut(
        id=log.id,
        actor_id=log.actor_id,
        action=log.action,
        resource_type=log.resource_type,
        resource_id=log.resource_id,
        metadata=log.metadata_ or {},
        created_at=log.created_at.isoformat(),
    )


def _gap_reason(answer: Answer) -> str | None:
    if answer.feedback in {"negative", "incorrect", "missing_source"}:
        return f"feedback:{answer.feedback}"
    if answer.evidence_score < 0.22:
        return "low_evidence"
    if not answer.citations:
        return "missing_citations"
    return None


def _gap_out(answer: Answer, reason: str) -> KnowledgeGapOut:
    return KnowledgeGapOut(
        id=answer.id,
        collection_id=answer.collection_id,
        question=answer.question,
        reason=reason,
        evidence_score=answer.evidence_score,
        feedback=answer.feedback,
        citations=answer.citations or [],
        created_at=answer.created_at.isoformat(),
    )


def _prefix_items(document: Document, items: list[str]) -> list[str]:
    prefix = document.author or document.project or document.title
    return [f"{prefix}: {item}" for item in items]


def _ensure_ragflow_dataset(db: Session, collection: Collection) -> str:
    metadata = dict(collection.metadata_ or {})
    dataset_id = metadata.get("ragflow_dataset_id")
    if dataset_id:
        try:
            RagFlowClient().get_dataset(str(dataset_id))
            return str(dataset_id)
        except RagFlowError:
            pass
    dataset = RagFlowClient().create_dataset(
        name=f"{collection.name}",
        description=collection.description,
        parser_config=metadata.get("parser_config"),
    )
    metadata["ragflow_dataset_id"] = dataset.get("id")
    metadata["ragflow_dataset_name"] = dataset.get("name")
    collection.metadata_ = metadata
    db.commit()
    return str(metadata["ragflow_dataset_id"])


def _ingest_with_ragflow_or_local(
    db: Session,
    collection: Collection,
    document: Document,
    data: bytes,
    content_type: str | None,
) -> None:
    if not get_settings().ragflow_enabled:
        _schedule_ingestion(document.id)
        return
    client = RagFlowClient()
    try:
        dataset_id = _ensure_ragflow_dataset(db, collection)
        document.status = DocumentStatus.parsing
        document.error_message = None
        db.commit()
        ragflow_filename, ragflow_data, ragflow_content_type, parser_metadata = prepare_for_ragflow(
            document.id,
            document.filename,
            data,
            content_type,
        )
        uploaded = client.upload_document(dataset_id, ragflow_filename, ragflow_data, ragflow_content_type)
        ragflow_doc_id = str(uploaded.get("id") or "")
        if not ragflow_doc_id or not _ragflow_document_belongs_to_dataset(client, dataset_id, ragflow_doc_id):
            ragflow_filename = f"{uuid.uuid4().hex[:8]}-{ragflow_filename}"
            uploaded = client.upload_document(dataset_id, ragflow_filename, ragflow_data, ragflow_content_type)
            ragflow_doc_id = str(uploaded.get("id") or "")
        if not ragflow_doc_id or not _ragflow_document_belongs_to_dataset(client, dataset_id, ragflow_doc_id):
            raise RagFlowError("RAGFlow uploaded document is not visible in the target dataset.")
        document.metadata_ = {
            **(document.metadata_ or {}),
            "ragflow_dataset_id": dataset_id,
            "ragflow_document_id": ragflow_doc_id,
            "ragflow_uploaded_filename": ragflow_filename,
            "ragflow_content_type": ragflow_content_type,
            **parser_metadata,
            "ragflow_document": uploaded,
        }
        db.commit()

        # Override RAGFlow's chunk method per document type.
        # PDF uses "paper" parser (layout + OCR), XLSX uses "table", etc.
        from app.ragflow.chunk_method import select_chunk_method

        chunk_method = select_chunk_method(ragflow_filename, ragflow_content_type)
        client.update_document_parser(dataset_id, ragflow_doc_id, chunk_method=chunk_method)

        client.parse_documents(dataset_id, [ragflow_doc_id])
        start_background_monitor(document.id, dataset_id, ragflow_doc_id)
    except Exception as exc:
        db.rollback()
        document = db.scalar(select(Document).where(Document.id == document.id))
        if document:
            document.status = DocumentStatus.failed
            document.error_message = f"RAGFlow ingestion failed: {exc}"
            db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _sync_ragflow_document_statuses(db: Session, collection: Collection) -> None:
    dataset_id = (collection.metadata_ or {}).get("ragflow_dataset_id")
    if not dataset_id:
        return
    try:
        remote_docs = RagFlowClient().list_documents(str(dataset_id))
    except Exception:
        return
    by_id = {str(item.get("id")): item for item in remote_docs}
    changed = False
    documents = db.scalars(select(Document).where(Document.collection_id == collection.id)).all()
    for document in documents:
        ragflow_doc_id = (document.metadata_ or {}).get("ragflow_document_id")
        remote = by_id.get(str(ragflow_doc_id))
        if remote:
            _apply_ragflow_document_status(document, remote)
            if document.status == DocumentStatus.ready:
                try:
                    synced = sync_ready_document_chunks(db, document)
                    document.metadata_ = {
                        **(document.metadata_ or {}),
                        "chunk_cache_count": synced,
                    }
                except Exception:
                    logger.warning("Failed to sync chunk cache for document %s", document.id, exc_info=True)
            changed = True
    if changed:
        db.commit()


def _apply_ragflow_document_status(document: Document, remote: dict) -> None:
    run = str(remote.get("run") or "").upper()
    if run == "DONE":
        document.status = DocumentStatus.ready
        document.error_message = None
    elif run in {"FAIL", "FAILED"}:
        document.status = DocumentStatus.failed
        document.error_message = str(remote.get("progress_msg") or "RAGFlow parsing failed")
    else:
        document.status = DocumentStatus.parsing
        progress = float(remote.get("progress") or 0)
    if run == "DONE":
        progress = 1.0
    elif run in {"FAIL", "FAILED"}:
        progress = float(remote.get("progress") or 0)
    document.metadata_ = {
        **(document.metadata_ or {}),
        "ragflow_status": remote,
        "analysis": {
            **((document.metadata_ or {}).get("analysis") or {}),
            "chunk_count": int(remote.get("chunk_count") or 0),
            "token_count": int(remote.get("token_count") or 0),
            "progress": progress,
            "progress_msg": remote.get("progress_msg") or "",
            "quality_warnings": _ragflow_status_quality_warnings(remote),
        },
    }


def _schedule_ingestion(document_id: str) -> None:
    if get_settings().ragflow_enabled:
        return
    if get_settings().ingestion_mode == "sync":
        from app.core.db import SessionLocal
        from app.rag.pipeline import IngestionPipeline

        with SessionLocal() as db:
            IngestionPipeline(db).run(document_id)
        return
    from app.workers.tasks import ingest_document

    ingest_document.delay(document_id)


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
