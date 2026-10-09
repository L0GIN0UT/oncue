"""Admin API (/admin/v1): для админ-панели, права проверяются на каждой ручке (§8 п.7).

Мутации — PUT с полной заменой состояния (идемпотентны по природе): повтор с тем же
``Idempotency-Key`` или без него не создаёт дублей и событий. Причина ``reason`` обязательна.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from gv_auth import Principal
from gv_core.pagination import MAX_PAGE_SIZE
from identity_service.api.common import ApiDeps, IdentityRoute, actor_of
from identity_service.application.dto import UserView
from identity_service.domain import RoleMatrix


class AdminUser(BaseModel):
    """Пользователь в админке."""

    id: UUID
    nickname: str
    email: str
    email_verified: bool
    display_name: str
    status: str
    roles: list[str]
    perms: list[str]
    created_at: datetime
    linked_providers: list[str]


class UsersPage(BaseModel):
    """Страница пользователей."""

    items: list[AdminUser]
    next_cursor: str | None


class RolesRequest(BaseModel):
    """Новый набор ролей пользователя."""

    roles: list[str] = Field(max_length=32)
    reason: str = Field(min_length=1, max_length=500)


class RoleMatrixBody(BaseModel):
    """Матрица ролей."""

    roles: dict[str, list[str]]
    permissions: list[str]


class RoleMatrixRequest(BaseModel):
    """Новая матрица ролей."""

    roles: dict[str, list[str]] = Field(max_length=64)
    reason: str = Field(min_length=1, max_length=500)


def _user(view: UserView) -> AdminUser:
    return AdminUser(
        id=view.id,
        nickname=view.nickname,
        email=view.email,
        email_verified=view.email_verified,
        display_name=view.display_name,
        status=view.status,
        roles=list(view.roles),
        perms=list(view.perms),
        created_at=view.created_at,
        linked_providers=[a.provider for a in view.linked_accounts],
    )


def _matrix(matrix: RoleMatrix) -> RoleMatrixBody:
    return RoleMatrixBody(
        roles={name: sorted(perms) for name, perms in sorted(matrix.roles.items())},
        permissions=sorted(matrix.permissions),
    )


IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)]


def build_admin_router(deps: ApiDeps) -> APIRouter:
    """Админские ручки."""
    uc, auth = deps.use_cases, deps.auth
    router = APIRouter(prefix="/admin/v1", tags=["admin"], route_class=IdentityRoute)
    CanView = Annotated[Principal, Depends(auth.require_perm("users.view"))]  # noqa: N806
    CanAssign = Annotated[Principal, Depends(auth.require_perm("roles.assign"))]  # noqa: N806
    CanManage = Annotated[Principal, Depends(auth.require_perm("roles.manage"))]  # noqa: N806

    @router.get("/users", response_model=UsersPage, summary="Поиск пользователей")
    async def list_users(
        _: CanView,
        q: Annotated[str | None, Query(max_length=64)] = None,
        cursor: Annotated[str | None, Query(max_length=1024)] = None,
        limit: Annotated[int | None, Query(ge=1, le=MAX_PAGE_SIZE)] = None,
    ) -> UsersPage:
        page = await uc.list_users(q, cursor=cursor, limit=limit)
        return UsersPage(items=[_user(v) for v in page.items], next_cursor=page.next_cursor)

    @router.get("/users/{user_id}", response_model=AdminUser, summary="Пользователь")
    async def get_user(user_id: UUID, _: CanView) -> AdminUser:
        return _user(await uc.get_user(user_id, include_deleted=True))

    @router.put("/users/{user_id}/roles", response_model=AdminUser, summary="Назначить роли")
    async def assign_roles(
        user_id: UUID,
        body: RolesRequest,
        principal: CanAssign,
        idempotency_key: IdempotencyKey = None,  # noqa: ARG001 — PUT идемпотентен сам
    ) -> AdminUser:
        await uc.assign_roles(actor_of(principal), user_id, frozenset(body.roles), body.reason)
        return _user(await uc.get_user(user_id))

    @router.get("/roles", response_model=RoleMatrixBody, summary="Матрица ролей")
    async def get_roles(_: CanAssign) -> RoleMatrixBody:
        return _matrix(await uc.get_role_matrix())

    @router.put("/roles", response_model=RoleMatrixBody, summary="Изменить матрицу ролей")
    async def put_roles(
        body: RoleMatrixRequest,
        principal: CanManage,
        idempotency_key: IdempotencyKey = None,  # noqa: ARG001 — PUT идемпотентен сам
    ) -> RoleMatrixBody:
        roles = {name: frozenset(perms) for name, perms in body.roles.items()}
        return _matrix(await uc.update_role_matrix(actor_of(principal), roles, body.reason))

    return router
