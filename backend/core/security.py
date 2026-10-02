"""JWT auth + RBAC dependencies (design doc 06). Local-issuer only (ADR-010)."""
from __future__ import annotations

import datetime as dt
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.database.session import get_db
from backend.models.entities import AuditLog, User

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

ALL_ROLES = ["Administrator", "QuestionGenerator", "AcademicReviewer",
             "SubjectExpert", "ExamController", "ReadOnly"]


def hash_password(pw: str) -> str:
    return pwd_ctx.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    return pwd_ctx.verify(pw, hashed)


def create_token(user: User) -> str:
    s = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user.id), "username": user.username, "role": user.role,
        "iat": now, "exp": now + dt.timedelta(minutes=s.access_token_ttl_minutes),
        "iss": "ai-qbe",
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        data = jwt.decode(creds.credentials, get_settings().secret_key,
                          algorithms=["HS256"], issuer="ai-qbe")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    user = db.get(User, int(data["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown/inactive user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: str):
    def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"role '{user.role}' lacks any of {list(roles)}")
        return user
    return dependency


def audit(db: Session, actor: User | None, action: str, entity: str,
          entity_id: str | int, detail: dict | None = None) -> None:
    import json
    db.add(AuditLog(actor_id=actor.id if actor else None, action=action,
                    entity=entity, entity_id=str(entity_id),
                    detail_json=json.dumps(detail) if detail else None))
    db.commit()
