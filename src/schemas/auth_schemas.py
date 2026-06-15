"""Schemas de autenticação — feature/login-area."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from src.services.permissions import VALID_ROLES


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: EmailStr
    name: str
    is_active: bool
    # Acesso + plano (jun/2026). Vão no /auth/me, /login e /register, então o
    # front sabe se mostra a área de admin e qual plano a pessoa tem.
    role: str = "user"
    plan: str = "free"
    created_at: datetime

    class Config:
        from_attributes = True


class UserAdminUpdate(BaseModel):
    """Campos que um admin pode alterar num usuário (PATCH parcial — todos
    opcionais). role é validado (segurança); plan é normalizado livre pra
    permitir introduzir tiers novos sem mexer no código."""
    plan: Optional[str] = Field(default=None, max_length=32)
    role: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in VALID_ROLES:
            raise ValueError(f"role inválido (use: {', '.join(VALID_ROLES)})")
        return v

    @field_validator("plan")
    @classmethod
    def _norm_plan(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if not v:
            raise ValueError("plan não pode ser vazio")
        return v


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ── Recuperação de senha ───────────────────────────────────────────────
class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=6, max_length=128)


class GenericMessage(BaseModel):
    """Resposta simples — usada onde só queremos sinalizar sucesso."""
    message: str
