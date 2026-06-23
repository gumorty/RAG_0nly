"""RAGFlow session lifecycle management.

Each user conversation in the management system maps to one RAGFlow
session within a RAGFlow Dialog (chat).  Sessions are created lazily
on the first user turn and reused for subsequent turns in the same
conversation.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.models.entities import Collection, RagflowSession
from app.ragflow.client import RagFlowClient

logger = logging.getLogger(__name__)


class RagflowSessionError(RuntimeError):
    pass


class RagflowSessionManager:
    """Manages the mapping between our conversations and RAGFlow sessions."""

    def __init__(self, db: DBSession) -> None:
        self.db = db
        self.client = RagFlowClient()

    def get_or_create_session(
        self,
        collection_id: str,
        user_id: str | None = None,
    ) -> tuple[str, str]:
        """Return ``(chat_id, session_id)`` for this collection.

        A RAGFlow session is created on first access and reused on
        subsequent calls.  The ``collection.metadata_`` must already
        contain ``ragflow_chat_id`` (set at collection creation time).
        """
        collection = self.db.scalar(
            select(Collection).where(Collection.id == collection_id)
        )
        if not collection:
            raise RagflowSessionError(f"Collection {collection_id} not found")

        metadata = collection.metadata_ or {}
        chat_id = metadata.get("ragflow_chat_id")
        if not chat_id:
            raise RagflowSessionError(
                f"Collection {collection_id} has no RAGFlow chat_id. "
                "Ensure RAGFLOW_ENABLE_CHAT_COMPLETIONS is on and the collection "
                "was created after this setting was enabled."
            )

        # Look for an existing session for this collection + user combo
        existing = self.db.scalar(
            select(RagflowSession).where(
                RagflowSession.collection_id == collection_id,
                RagflowSession.user_id == user_id,
            ).order_by(RagflowSession.updated_at.desc())
        )
        if existing:
            existing.turn_count += 1
            existing.updated_at = datetime.utcnow()
            self.db.commit()
            return str(chat_id), existing.session_id

        # Create a new session in RAGFlow
        session_name = f"Session-{user_id[:8]}" if user_id else "Management-Session"
        try:
            remote = self.client.create_session(str(chat_id), name=session_name)
        except Exception as exc:
            raise RagflowSessionError(
                f"Failed to create RAGFlow session for chat {chat_id}: {exc}"
            ) from exc

        remote_session_id = str(remote.get("id") or "")
        if not remote_session_id:
            raise RagflowSessionError(
                "RAGFlow created a session but returned no ID"
            )

        record = RagflowSession(
            collection_id=collection_id,
            chat_id=str(chat_id),
            session_id=remote_session_id,
            user_id=user_id,
            turn_count=1,
            title=session_name,
        )
        self.db.add(record)
        self.db.commit()
        logger.info(
            "Created RAGFlow session %s for collection %s (user=%s)",
            remote_session_id,
            collection_id,
            user_id,
        )
        return str(chat_id), remote_session_id

    def update_session(self, session_id: str) -> None:
        """Increment turn count and bump the timestamp."""
        record = self.db.scalar(
            select(RagflowSession).where(RagflowSession.session_id == session_id)
        )
        if record:
            record.turn_count += 1
            record.updated_at = datetime.utcnow()
            self.db.commit()

    def reset_session(self, session_id: str) -> None:
        """Delete the RAGFlow remote session and our local record."""
        record = self.db.scalar(
            select(RagflowSession).where(RagflowSession.session_id == session_id)
        )
        if record:
            try:
                self.client.delete_chat(record.chat_id)  # chat_id here, session_id there
            except Exception:
                logger.warning("Failed to delete RAGFlow session %s", session_id, exc_info=True)
            self.db.delete(record)
            self.db.commit()

    def list_sessions(
        self,
        collection_id: str,
        user_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """List active RAGFlow sessions for a collection."""
        stmt = (
            select(RagflowSession)
            .where(RagflowSession.collection_id == collection_id)
            .order_by(RagflowSession.updated_at.desc())
            .limit(limit)
        )
        if user_id:
            stmt = stmt.where(RagflowSession.user_id == user_id)
        records = self.db.scalars(stmt).all()
        return [
            {
                "session_id": r.session_id,
                "chat_id": r.chat_id,
                "turn_count": r.turn_count,
                "title": r.title,
                "created_at": r.created_at.isoformat(),
                "updated_at": r.updated_at.isoformat(),
            }
            for r in records
        ]
