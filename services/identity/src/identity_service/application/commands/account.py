"""Согласия и удаление аккаунта."""

from __future__ import annotations

from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import InvalidInputError, NotFoundError, UnauthenticatedError
from identity_service.application.commands.auth import (
    revoke_all_sessions,
    revoke_quietly,
)
from identity_service.application.config import AuthConfig
from identity_service.application.ports import (
    PasswordHasher,
    RevocationStore,
    UnitOfWorkFactory,
)
from identity_service.domain import Consent, ConsentRequirement, Provider

log = structlog.get_logger(__name__)


class GrantConsents:
    """Принять согласия (например, новую редакцию условий)."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, config: AuthConfig) -> None:
        self._uow, self._clock, self._config = uow, clock, config

    async def __call__(self, user_id: UUID, consents: tuple[ConsentRequirement, ...]) -> None:
        """Сохранить согласия. Принимаются только редакции из конфигурации.

        Raises:
            InvalidInputError: ``auth.consent_unknown``.
        """
        known = set(self._config.required_consents)
        unknown = [f"{c.kind}:{c.version}" for c in consents if c not in known]
        if unknown:
            raise InvalidInputError(
                "неизвестные согласия", code="auth.consent_unknown", details={"unknown": unknown}
            )
        now = self._clock.now()
        async with self._uow() as uow:
            await uow.consents.add(Consent(user_id, c.kind, c.version, now) for c in consents)
            await uow.commit()


class DeleteAccount:
    """Удалить аккаунт с подтверждением паролем.

    Ник и e-mail освобождаются, пароль и привязки удаляются, сессии отзываются; согласия
    остаются как юридическая запись. Другие сервисы чистят свои данные по ``UserDeleted``.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        hasher: PasswordHasher,
        revocation: RevocationStore,
        clock: Clock,
    ) -> None:
        self._uow, self._hasher, self._revocation, self._clock = uow, hasher, revocation, clock

    async def __call__(self, user_id: UUID, password: str) -> None:
        """Удалить.

        Raises:
            UnauthenticatedError: ``auth.invalid_credentials`` — неверный пароль.
            NotFoundError: аккаунта нет или он уже удалён.
        """
        async with self._uow() as uow:
            user = await uow.users.get(user_id)
            if user is None or not user.is_active:
                raise NotFoundError("аккаунт не найден", code="user.not_found")
            password_hash = await uow.credentials.get_password_hash(user_id)
            if password_hash is None or not await self._hasher.verify(password_hash, password):
                raise UnauthenticatedError("неверный пароль", code="auth.invalid_credentials")
            for identity in await uow.identities.list_for_user(user_id):
                await uow.identities.remove(user_id, Provider(identity.provider))
            await uow.credentials.delete(user_id)
            user.delete(self._clock.now())
            await uow.users.save(user)
            revoked = await revoke_all_sessions(uow, user_id, self._clock, "account_deleted")
            uow.track(user)
            await uow.commit()
        await revoke_quietly(self._revocation, revoked)
        log.info("auth.account_deleted", user_id=str(user_id))
