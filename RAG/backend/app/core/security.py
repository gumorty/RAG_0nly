import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from passlib.context import CryptContext

from app.core.config import get_settings

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def verify_api_key(api_key: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(api_key), expected_hash)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    return pwd_context.verify(password, password_hash)


def validate_password_strength(password: str) -> None:
    if len(password) < 10:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="密码至少需要 10 位")
    checks = [
        (r"[A-Z]", "至少包含一个大写字母"),
        (r"[a-z]", "至少包含一个小写字母"),
        (r"\d", "至少包含一个数字"),
        (r"[^A-Za-z0-9]", "至少包含一个特殊字符"),
    ]
    for pattern, message in checks:
        if not re.search(pattern, password):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def create_access_token(user_id: str, role: str, token_version: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return _encode_jwt(
        {
            "sub": user_id,
            "role": role,
            "typ": "access",
            "ver": token_version,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=settings.access_token_minutes)).timestamp()),
        }
    )


def create_refresh_token(user_id: str, token_version: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return _encode_jwt(
        {
            "sub": user_id,
            "typ": "refresh",
            "ver": token_version,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=settings.refresh_token_days)).timestamp()),
        }
    )


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    payload = _decode_jwt(token)
    if payload.get("typ") != expected_type:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    return payload


def _encode_jwt(payload: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64_json(header)}.{_b64_json(payload)}"
    signature = hmac.new(_jwt_secret(), signing_input.encode("utf-8"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


def _decode_jwt(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    signing_input = f"{parts[0]}.{parts[1]}"
    expected = hmac.new(_jwt_secret(), signing_input.encode("utf-8"), hashlib.sha256).digest()
    actual = _b64_decode(parts[2])
    if not hmac.compare_digest(expected, actual):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token signature")
    try:
        payload = json.loads(_b64_decode(parts[1]).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload") from exc
    exp = int(payload.get("exp", 0))
    if exp < int(datetime.now(timezone.utc).timestamp()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    return payload


def _b64_json(value: dict[str, Any]) -> str:
    return _b64(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _jwt_secret() -> bytes:
    return get_settings().app_secret.encode("utf-8")
