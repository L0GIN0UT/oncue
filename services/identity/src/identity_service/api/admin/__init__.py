"""Admin API (/admin/v1): для админ-панели, права проверяются на каждой ручке (§8 п.7).

Права: ``admin.access`` + конкретное право на каждой ручке. Мутации требуют
``Idempotency-Key`` (gv_service.IdempotencyKey): ключ и ответ хранятся в БД в той же
транзакции, что и изменение (gv_db.idempotency); повтор возвращает сохранённый ответ, тот же
ключ с другим телом — 409 ``idempotency.key_reused``. Причина ``reason`` обязательна (3–500).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from gv_auth import Principal
from gv_core.pagination import MAX_PAGE_SIZE
from gv_service import IdempotencyKey, Reason, problem_responses
from identity_service.api.common import ApiDeps, actor_of
from identity_service.application.commands.auth import ADMIN_ACCESS_PERM
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
    reason: Reason


class RoleMatrixBody(BaseModel):
    """Матрица ролей."""

    roles: dict[str, list[str]]
    permissions: list[str]


class RoleMatrixRequest(BaseModel):
    """Новая матрица ролей."""

    roles: dict[str, list[str]] = Field(max_length=64)
    reason: Reason


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


def build_admin_router(deps: ApiDeps) -> APIRouter:
    """Админские ручки."""
    uc, auth = deps.use_cases, deps.auth
    router = APIRouter(prefix="/admin/v1", tags=["admin"])
    CanView = Annotated[  # noqa: N806
        Principal, Depends(auth.require_perm(ADMIN_ACCESS_PERM, "users.view"))
    ]
    CanAssign = Annotated[  # noqa: N806
        Principal, Depends(auth.require_perm(ADMIN_ACCESS_PERM, "roles.assign"))
    ]
    CanManage = Annotated[  # noqa: N806
        Principal, Depends(auth.require_perm(ADMIN_ACCESS_PERM, "roles.manage"))
    ]
    mutation_errors = problem_responses(401, 403, 404, 409, 422)

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

    @router.put(
        "/users/{user_id}/roles",
        response_model=AdminUser,
        summary="Назначить роли",
        description="Понижение прав отзывает все сессии пользователя.",
        responses=mutation_errors,
    )
    async def assign_roles(
        user_id: UUID, body: RolesRequest, principal: CanAssign, key: IdempotencyKey
    ) -> AdminUser:
        result = await uc.assign_roles(
            actor_of(principal), user_id, frozenset(body.roles), body.reason, key
        )
        return _user(UserView.from_json(result))

    @router.get("/roles", response_model=RoleMatrixBody, summary="Матрица ролей")
    async def get_roles(_: CanAssign) -> RoleMatrixBody:
        return _matrix(await uc.get_role_matrix())

    @router.put(
        "/roles",
        response_model=RoleMatrixBody,
        summary="Изменить матрицу ролей",
        description="Пользователи, потерявшие права, теряют и все сессии.",
        responses=mutation_errors,
    )
    async def put_roles(
        body: RoleMatrixRequest, principal: CanManage, key: IdempotencyKey
    ) -> RoleMatrixBody:
        roles = {name: frozenset(perms) for name, perms in body.roles.items()}
        result = await uc.update_role_matrix(actor_of(principal), roles, body.reason, key)
        return RoleMatrixBody.model_validate(result)

    return router
