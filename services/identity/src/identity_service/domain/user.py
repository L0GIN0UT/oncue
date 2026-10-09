"""Агрегат «Пользователь»."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from gv_core.domain import AggregateRoot
from gv_core.errors import DomainError
from identity_service.domain.events import (
    ExternalAccountLinked,
    ExternalAccountUnlinked,
    UserDeleted,
    UserRegistered,
    UserRolesChanged,
)
from identity_service.domain.roles import RoleMatrix
from identity_service.domain.values import DisplayName, EmailAddress, Nickname, Provider


class UserStatus(StrEnum):
    """Статус аккаунта."""

    ACTIVE = "active"
    DELETED = "deleted"


class User(AggregateRoot[UUID]):
    """Учётная запись: никнейм + e-mail + пароль (D2), роли.

    ``version`` растёт на каждое опубликованное изменение и попадает в события: потребители
    отбрасывают события с версией не больше уже применённой. Репозиторий использует её же
    для оптимистичной блокировки.
    """

    def __init__(
        self,
        id: UUID,  # noqa: A002
        *,
        nickname: Nickname,
        email: EmailAddress,
        display_name: DisplayName,
        locale: str | None,
        status: UserStatus,
        roles: frozenset[str],
        roles_version: int,
        email_verified_at: datetime | None,
        created_at: datetime,
        deleted_at: datetime | None,
        version: int,
    ) -> None:
        super().__init__(id, version)
        self.nickname = nickname
        self.email = email
        self.display_name = display_name
        self.locale = locale
        self.status = status
        self.roles = roles
        self.roles_version = roles_version
        self.email_verified_at = email_verified_at
        self.created_at = created_at
        self.deleted_at = deleted_at
        self.persisted_version = version
        """Версия на момент загрузки — условие оптимистичной блокировки при сохранении."""

    @classmethod
    def register(
        cls,
        id: UUID,  # noqa: A002
        *,
        nickname: Nickname,
        email: EmailAddress,
        display_name: DisplayName,
        locale: str | None,
        roles: frozenset[str],
        now: datetime,
    ) -> User:
        """Новый пользователь (e-mail ещё не подтверждён)."""
        user = cls(
            id,
            nickname=nickname,
            email=email,
            display_name=display_name,
            locale=locale,
            status=UserStatus.ACTIVE,
            roles=roles,
            roles_version=0,
            email_verified_at=None,
            created_at=now,
            deleted_at=None,
            version=1,
        )
        user.persisted_version = 0
        user.record(
            UserRegistered(
                aggregate_id=str(id),
                user_id=id,
                nickname=nickname.value,
                display_name=display_name.value,
                locale=locale,
                version=1,
            )
        )
        return user

    @property
    def is_active(self) -> bool:
        """Аккаунт не удалён."""
        return self.status is UserStatus.ACTIVE

    @property
    def email_verified(self) -> bool:
        """E-mail подтверждён."""
        return self.email_verified_at is not None

    def _ensure_active(self) -> None:
        if not self.is_active:
            raise DomainError("аккаунт удалён", code="user.deleted")

    def _next_version(self) -> int:
        self.version += 1
        return self.version

    def verify_email(self, now: datetime) -> bool:
        """Отметить e-mail подтверждённым. ``False`` — уже был подтверждён."""
        self._ensure_active()
        if self.email_verified_at is not None:
            return False
        self.email_verified_at = now
        return True

    def change_roles(
        self,
        roles: frozenset[str],
        matrix: RoleMatrix,
        *,
        actor_id: UUID | None,
        reason: str | None,
    ) -> bool:
        """Заменить набор ролей. ``False`` — набор не изменился (событие не публикуется)."""
        self._ensure_active()
        roles = matrix.require_known(roles)
        if roles == self.roles:
            return False
        self.roles = roles
        self.announce_perms(matrix, actor_id=actor_id, reason=reason)
        return True

    def announce_perms(
        self, matrix: RoleMatrix, *, actor_id: UUID | None, reason: str | None
    ) -> None:
        """Опубликовать снимок ролей и прав (после смены ролей или матрицы)."""
        self._ensure_active()
        self.roles_version += 1
        self.record(
            UserRolesChanged(
                aggregate_id=str(self.id),
                user_id=self.id,
                roles=tuple(sorted(self.roles)),
                perms=tuple(sorted(matrix.effective_perms(self.roles))),
                actor_id=actor_id,
                reason=reason,
                version=self._next_version(),
            )
        )

    def link_provider(self, provider: Provider) -> None:
        """Зафиксировать привязку внешнего сервиса."""
        self._ensure_active()
        self.record(
            ExternalAccountLinked(
                aggregate_id=str(self.id),
                user_id=self.id,
                provider=provider.value,
                version=self._next_version(),
            )
        )

    def unlink_provider(self, provider: Provider) -> None:
        """Зафиксировать отвязку внешнего сервиса."""
        self._ensure_active()
        self.record(
            ExternalAccountUnlinked(
                aggregate_id=str(self.id),
                user_id=self.id,
                provider=provider.value,
                version=self._next_version(),
            )
        )

    def delete(self, now: datetime) -> None:
        """Удалить аккаунт: освободить ник и e-mail, снять роли.

        Учётные данные, сессии и привязки удаляет use-case через свои репозитории.
        """
        self._ensure_active()
        self.status = UserStatus.DELETED
        self.deleted_at = now
        self.nickname = Nickname.tombstone(self.id.hex)
        self.email = EmailAddress.tombstone(self.id.hex)
        self.display_name = DisplayName("deleted")
        self.roles = frozenset()
        self.record(
            UserDeleted(
                aggregate_id=str(self.id),
                user_id=self.id,
                deleted_at=now,
                version=self._next_version(),
            )
        )
