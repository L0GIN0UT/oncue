"""Запросы identity (только чтение)."""

from __future__ import annotations

import dataclasses
from typing import Any
from uuid import UUID

from gv_core.errors import InvalidInputError, NotFoundError
from gv_core.pagination import Page, clamp_limit, decode_cursor, encode_cursor
from identity_service.application.dto import LinkedAccountView, UserView
from identity_service.application.ports import (
    IdentityUnitOfWork,
    SigningKeyProvider,
    UnitOfWorkFactory,
)
from identity_service.domain import ConsentRequirement, RoleMatrix, User


def build_user_view(
    user: User, matrix: RoleMatrix, linked: tuple[LinkedAccountView, ...] = ()
) -> UserView:
    """Представление пользователя с действующими правами."""
    return UserView(
        id=user.id,
        nickname=user.nickname.value,
        email=user.email.value,
        email_verified=user.email_verified,
        display_name=user.display_name.value,
        locale=user.locale,
        status=user.status.value,
        roles=tuple(sorted(user.roles)),
        perms=tuple(sorted(matrix.effective_perms(user.roles))),
        created_at=user.created_at,
        linked_accounts=linked,
    )


async def linked_accounts(uow: IdentityUnitOfWork, user_id: UUID) -> tuple[LinkedAccountView, ...]:
    """Привязки пользователя (без внешних идентификаторов)."""
    return tuple(
        LinkedAccountView(provider=i.provider.value, display=i.display, linked_at=i.linked_at)
        for i in await uow.identities.list_for_user(user_id)
    )


class GetUser:
    """Пользователь с правами, привязками и согласиями (профиль владельца и админка)."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def __call__(self, user_id: UUID, *, include_deleted: bool = False) -> UserView:
        """Найти.

        Raises:
            NotFoundError: ``user.not_found``.
        """
        async with self._uow() as uow:
            user = await uow.users.get(user_id)
            if user is None or (not user.is_active and not include_deleted):
                raise NotFoundError("пользователь не найден", code="user.not_found")
            matrix = await uow.roles.get_matrix()
            view = build_user_view(user, matrix, await linked_accounts(uow, user_id))
            consents = tuple(
                sorted(
                    ConsentRequirement(c.kind, c.version)
                    for c in await uow.consents.list_for_user(user_id)
                )
            )
        return dataclasses.replace(view, consents=consents)


class ListLinkedAccounts:
    """Привязанные внешние сервисы пользователя."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def __call__(self, user_id: UUID) -> tuple[LinkedAccountView, ...]:
        """Список привязок."""
        async with self._uow() as uow:
            return await linked_accounts(uow, user_id)


class ListUsers:
    """Поиск пользователей для админки (курсорная пагинация по id)."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def __call__(
        self, query: str | None, *, cursor: str | None, limit: int | None
    ) -> Page[UserView]:
        """Страница пользователей."""
        size = clamp_limit(limit)
        after: UUID | None = None
        if cursor:
            raw = decode_cursor(cursor).get("id")
            try:
                after = UUID(str(raw))
            except ValueError:
                raise InvalidInputError("неверный курсор", code="pagination.bad_cursor") from None
        q = (query or "").strip()[:64] or None
        async with self._uow() as uow:
            users = await uow.users.search(q, after=after, limit=size + 1)
            matrix = await uow.roles.get_matrix()
        page = users[:size]
        next_cursor = encode_cursor({"id": str(page[-1].id)}) if len(users) > size else None
        return Page([build_user_view(u, matrix) for u in page], next_cursor)


class GetRoleMatrix:
    """Текущая матрица ролей."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def __call__(self) -> RoleMatrix:
        """Матрица."""
        async with self._uow() as uow:
            return await uow.roles.get_matrix()


class GetJwks:
    """Публичные ключи подписи (все неистёкшие — ротация без простоя)."""

    def __init__(self, keys: SigningKeyProvider) -> None:
        self._keys = keys

    def __call__(self) -> dict[str, Any]:
        """JWKS."""
        return self._keys.jwks()
