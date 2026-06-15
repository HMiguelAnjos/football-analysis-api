"""
Permissões por role (jun/2026) — estrutura CENTRAL e fácil de expandir.

Pra adicionar uma permissão nova: declare a constante e some-a ao set do(s)
role(s) em ROLE_PERMISSIONS. Os gates (dependencies no main, menu/rotas no
front) passam a respeitar automaticamente. NUNCA espalhar checagem de role
solta pelo código — sempre via has_permission().

Espelhado no front em src/auth/permissions.ts (mantar os dois em sincronia).
"""
from __future__ import annotations

# ── Permissões disponíveis ───────────────────────────────────────────────
VIEW_PERFORMANCE = "view_performance"             # aba Performance
VIEW_USERS = "view_users"                         # aba Usuários (gestão)
MANAGE_RECOMMENDATIONS = "manage_recommendations" # criar/remover entradas recomendadas

# ── Role → conjunto de permissões (ADMINISTRATIVAS) ──────────────────────
# admin      : acesso total
# analyst    : performance + recomendações de entradas
# influencer : NENHUMA permissão admin (igual user) — mas com acesso premium
#              de cortesia (ver has_premium_access). Parceiro/divulgador que
#              não paga e enxerga o conteúdo top, sem ver nada de admin.
# user       : sem área administrativa (vê só a aba pública de entradas)
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin":      {VIEW_PERFORMANCE, VIEW_USERS, MANAGE_RECOMMENDATIONS},
    "analyst":    {VIEW_PERFORMANCE, MANAGE_RECOMMENDATIONS},
    "influencer": set(),
    "user":       set(),
}

# Roles válidos que um admin pode atribuir — fonte única pra validação de
# input (usado pelo schema UserAdminUpdate).
VALID_ROLES: tuple[str, ...] = tuple(ROLE_PERMISSIONS.keys())

# Roles com acesso PREMIUM de cortesia (sem plano pago). Influencer entra
# aqui; admin/analyst obviamente já veem tudo. Quando o gating por plano for
# implementado, basta consultar has_premium_access() em vez de checar plano
# solto — fonte única do "esse usuário enxerga o conteúdo top".
_COURTESY_PREMIUM_ROLES = {"admin", "analyst", "influencer"}
# Plano pago de topo (hoje os planos ainda não diferenciam conteúdo; quando
# diferenciarem, é este o tier que libera o premium).
_TOP_PAID_PLAN = "premium"


def role_permissions(role: str | None) -> set[str]:
    """Permissões de um role. Role desconhecido/None → nenhuma permissão."""
    return ROLE_PERMISSIONS.get(role or "user", set())


def has_permission(user, permission: str) -> bool:
    """True se o user tem a permissão. Tolera user None / sem atributo role."""
    return permission in role_permissions(getattr(user, "role", None))


def has_premium_access(user) -> bool:
    """True se o usuário enxerga o conteúdo PREMIUM/top — por plano pago
    (premium) OU por cortesia de role (influencer/admin/analyst). Hook central
    pro futuro gating por plano; hoje nada gateia, mas a intenção já fica
    registrada e num lugar só. Tolera user None."""
    role = getattr(user, "role", None)
    plan = getattr(user, "plan", None)
    return role in _COURTESY_PREMIUM_ROLES or plan == _TOP_PAID_PLAN
