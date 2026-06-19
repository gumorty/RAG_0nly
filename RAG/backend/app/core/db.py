from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import entities  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()
    _bootstrap_admin()
    _bootstrap_default_strategy()


def _apply_lightweight_migrations() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("users")}
    migrations = []
    if "password_hash" not in columns:
        migrations.append("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)")
    if "token_version" not in columns:
        migrations.append("ALTER TABLE users ADD COLUMN token_version INTEGER DEFAULT 1")
    if "last_login_at" not in columns:
        migrations.append("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP")
    if not migrations:
        return
    with engine.begin() as connection:
        for statement in migrations:
            connection.execute(text(statement))
        connection.execute(text("UPDATE users SET token_version = 1 WHERE token_version IS NULL"))


def _bootstrap_admin() -> None:
    from sqlalchemy import select

    from app.core.security import hash_api_key, hash_password
    from app.models.entities import User, UserRole

    settings = get_settings()
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == settings.bootstrap_admin_email))
        if existing:
            changed = False
            if not existing.password_hash:
                existing.password_hash = hash_password(settings.bootstrap_admin_password)
                changed = True
            if not existing.api_key_hash:
                existing.api_key_hash = hash_api_key(settings.bootstrap_admin_api_key)
                changed = True
            if existing.token_version is None:
                existing.token_version = 1
                changed = True
            if changed:
                db.commit()
            return
        db.add(
            User(
                email=settings.bootstrap_admin_email,
                name=settings.bootstrap_admin_name,
                role=UserRole.admin,
                api_key_hash=hash_api_key(settings.bootstrap_admin_api_key),
                password_hash=hash_password(settings.bootstrap_admin_password),
                token_version=1,
            )
        )
        db.commit()


def _bootstrap_default_strategy() -> None:
    from sqlalchemy import select

    from app.models.entities import RagStrategyPreset

    settings = get_settings()
    with SessionLocal() as db:
        existing = db.scalar(select(RagStrategyPreset).where(RagStrategyPreset.is_default.is_(True)))
        if existing:
            return
        db.add(
            RagStrategyPreset(
                name="Hybrid Baseline",
                description="Dense + keyword retrieval, RRF fusion, optional reranker, grounded generation.",
                is_default=True,
                config={
                    "dense_top_k": settings.retrieval_dense_top_k,
                    "keyword_top_k": settings.retrieval_keyword_top_k,
                    "final_top_k": settings.retrieval_final_top_k,
                    "use_query_rewrite": True,
                    "use_reranker": settings.enable_reranker,
                    "use_parent_context": True,
                    "context_window_chunks": 1,
                    "min_evidence_score": settings.min_answer_evidence_score,
                },
            )
        )
        db.commit()
