from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import decode_token, hash_api_key
from app.models.entities import AuditLog, User, UserRole


def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> User:
    user: User | None = None
    if authorization and authorization.lower().startswith("bearer "):
        payload = decode_token(authorization.split(" ", 1)[1].strip(), "access")
        user = db.scalar(select(User).where(User.id == payload.get("sub"), User.is_active.is_(True)))
        if user and int(payload.get("ver", 0)) != int(user.token_version or 1):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")
    elif x_api_key:
        user = db.scalar(select(User).where(User.api_key_hash == hash_api_key(x_api_key), User.is_active.is_(True)))
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return user


def require_roles(*roles: UserRole):
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return current_user

    return dependency


def can_read_acl(user: User, acl: list[str]) -> bool:
    if user.role in {UserRole.admin, UserRole.maintainer}:
        return True
    effective_acl = acl or ["public"]
    return "public" in effective_acl or user.id in effective_acl or user.email in effective_acl or user.role.value in effective_acl


def write_audit(
    db: Session,
    actor: User | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    metadata: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_=metadata or {},
        )
    )
