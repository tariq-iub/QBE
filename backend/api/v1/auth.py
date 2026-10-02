"""Auth endpoints: local JWT login + bootstrap admin creation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.security import create_token, hash_password, verify_password
from backend.database.session import get_db
from backend.models.entities import User
from backend.schemas.api_dto import TokenRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
def login(body: TokenRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=body.username).one_or_none()
    if user is None or not user.is_active or not verify_password(body.password,
                                                                 user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    return TokenResponse(access_token=create_token(user), role=user.role)


def ensure_bootstrap_admin(db: Session) -> None:
    s = get_settings()
    if not s.bootstrap_admin_password:
        return
    if db.query(User).filter_by(username=s.bootstrap_admin_username).first():
        return
    db.add(User(username=s.bootstrap_admin_username,
                password_hash=hash_password(s.bootstrap_admin_password),
                role="Administrator"))
    db.commit()
