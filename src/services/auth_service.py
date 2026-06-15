"""
AuthService — hashing de senha + JWT + CRUD de usuário.
feature/login-area. Sem dependência de rede; usa o banco via Session.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.config import (
    JWT_ALGORITHM,
    JWT_EXPIRE_MINUTES,
    JWT_SECRET,
    PASSWORD_RESET_EXPIRE_MINUTES,
)
from src.db.models import User

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Senha ────────────────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd.verify(plain, hashed)
    except Exception:
        return False


# ── JWT ──────────────────────────────────────────────────────────────────
def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[int]:
    """Retorna o user_id do token, ou None se inválido/expirado."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except (JWTError, ValueError):
        return None


# ── CRUD ─────────────────────────────────────────────────────────────────
def get_user_by_email(db: Session, email: str) -> Optional[User]:
    email = (email or "").strip().lower()
    return db.scalar(select(User).where(User.email == email))


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.get(User, user_id)


def create_user(db: Session, *, email: str, password: str, name: str = "") -> User:
    user = User(
        email=email.strip().lower(),
        name=name.strip(),
        hashed_password=hash_password(password),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, email: str, password: str) -> Optional[User]:
    user = get_user_by_email(db, email)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


# ── Admin (jun/2026) ─────────────────────────────────────────────────────
def is_admin(user) -> bool:
    """Predicado de acesso admin. Tolera user None / sem role (→ False).
    Fonte única usada pela dependency get_current_admin."""
    return getattr(user, "role", "user") == "admin"


def list_users(
    db: Session,
    *,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[User]:
    """Lista usuários pra área de admin (mais recentes primeiro). `search`
    filtra por email OU nome (case-insensitive, substring)."""
    q = select(User)
    if search and search.strip():
        like = f"%{search.strip().lower()}%"
        q = q.where(
            or_(
                func.lower(User.email).like(like),
                func.lower(User.name).like(like),
            )
        )
    q = q.order_by(User.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(q).all())


def update_user_admin(
    db: Session,
    user: User,
    *,
    plan: Optional[str] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> User:
    """Atualiza campos administrativos de um usuário (PATCH parcial). Só
    altera o que vier não-None."""
    if plan is not None:
        user.plan = plan
    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    db.commit()
    db.refresh(user)
    return user


def promote_admins_by_email(db: Session, emails: list[str]) -> int:
    """Bootstrap: garante role='admin' pros emails informados. Idempotente —
    só toca em quem ainda não é admin. Não cria usuário (precisa existir).
    Retorna quantos foram promovidos."""
    promoted = 0
    for email in emails:
        user = get_user_by_email(db, email)
        if user is not None and user.role != "admin":
            user.role = "admin"
            promoted += 1
    if promoted:
        db.commit()
    return promoted


# ── Reset de senha ──────────────────────────────────────────────────────
# Reusa o JWT_SECRET, mas marca o payload com `type=reset` pra separar do
# access token — assim um access token NUNCA pode ser usado pra resetar
# senha (e vice-versa). TTL curto (PASSWORD_RESET_EXPIRE_MINUTES, 1h padrão).

def create_password_reset_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "type": "reset", "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_password_reset_token(token: str) -> Optional[int]:
    """Retorna o user_id se token de reset for válido/não-expirado, senão None."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "reset":
            return None
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except (JWTError, ValueError):
        return None


def update_password(db: Session, user: User, new_password: str) -> None:
    user.hashed_password = hash_password(new_password)
    db.commit()
