"""Сборка use-cases social (composition): общая для API, consumer и relay."""

from __future__ import annotations

from collections.abc import Callable
from functools import cached_property

from sqlalchemy.ext.asyncio import AsyncSession

from gv_core.errors import DependencyUnavailableError
from gv_core.random import Random, SystemRandom
from gv_service import Infrastructure
from social_service.application.config_admin import ConfigCommands, ConfigQueries
from social_service.application.friends import FriendsService
from social_service.application.friends_projections import FriendsProjectionService
from social_service.application.ports.config import ConfigProvider, ConfigUnavailableError
from social_service.application.ports.friends import AttemptLimiter, PresenceStore
from social_service.application.sanctions import SanctionService
from social_service.domain.config import SocialConfig
from social_service.infrastructure.adapters import (
    RedisAttemptLimiter,
    RedisPresenceStore,
    UnconfiguredLimiter,
    UnconfiguredPresence,
)
from social_service.infrastructure.config_validation import validate_config_body
from social_service.infrastructure.db.repositories_config import DbConfigProvider
from social_service.infrastructure.db.repositories_sanctions import SqlSanctionsUnitOfWork
from social_service.infrastructure.db.uow_friends import SqlFriendsUnitOfWork
from social_service.settings import Settings


class SocialContainer:
    """Use-cases сервиса поверх инфраструктуры процесса.

    Зависимости, которые подменяют тесты (``limiter``, ``presence``, ``rng``), читаются при
    каждой сборке use-case, поэтому подмена после создания приложения действует.
    """

    def __init__(self, infra: Infrastructure) -> None:
        if not isinstance(infra.settings, Settings):
            raise TypeError("ожидаются настройки social")
        self.infra = infra
        self.settings: Settings = infra.settings
        self.rng: Random = SystemRandom()

    def uow_factory(
        self, session: AsyncSession | None = None
    ) -> Callable[[], SqlFriendsUnitOfWork]:
        """Фабрика UoW: своя транзакция или SAVEPOINT в транзакции consumer."""
        infra = self.infra
        producer = self.settings.service_name

        def factory() -> SqlFriendsUnitOfWork:
            if session is not None:
                return SqlFriendsUnitOfWork(producer=producer, clock=infra.clock, session=session)
            if infra.sessions is None:
                raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
            return SqlFriendsUnitOfWork(
                producer=producer, clock=infra.clock, sessions=infra.sessions
            )

        return factory

    def sanctions(self, session: AsyncSession | None = None) -> SanctionService:
        """Санкции (ADR 0052): ``SanctionGranter`` и ``SanctionReader`` в одном объекте.

        С ``session`` работает в уже открытой транзакции (SAVEPOINT): кейс модерации и
        санкция фиксируются атомарно вместе с событием в outbox.
        """
        infra = self.infra
        producer = self.settings.service_name

        def factory() -> SqlSanctionsUnitOfWork:
            if session is not None:
                return SqlSanctionsUnitOfWork(producer=producer, clock=infra.clock, session=session)
            if infra.sessions is None:
                raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
            return SqlSanctionsUnitOfWork(
                producer=producer, clock=infra.clock, sessions=infra.sessions
            )

        return SanctionService(factory, self.config, infra.clock)

    @cached_property
    def config(self) -> ConfigProvider:
        """Действующий конфиг: БД с коротким кэшем (нет БД - всегда ``ConfigUnavailableError``)."""
        if self.infra.sessions is None:
            return _NoDatabaseConfig()
        return DbConfigProvider(
            self.infra.sessions, self.infra.clock, self.settings.social.config_cache_ttl_s
        )

    @cached_property
    def limiter(self) -> AttemptLimiter:
        """Лимитер попыток: Redis; без Redis операции с лимитом закрыты (fail-closed)."""
        retry = self.settings.social.limiter_retry_after_s
        if self.infra.redis is None:
            return UnconfiguredLimiter(retry)
        return RedisAttemptLimiter(self.infra.redis, retry_after_s=retry)

    @cached_property
    def presence(self) -> PresenceStore:
        """Присутствие: Redis."""
        retry = self.settings.social.limiter_retry_after_s
        if self.infra.redis is None:
            return UnconfiguredPresence(retry)
        return RedisPresenceStore(self.infra.redis, retry_after_s=retry)

    @property
    def friends(self) -> FriendsService:
        """Друзья, блокировки, приватность, присутствие, приглашения."""
        return FriendsService(
            self.uow_factory(),
            self.config,
            self.infra.clock,
            self.rng,
            limiter=self.limiter,
            presence=self.presence,
            presence_burst=self.settings.social.presence_burst,
            sanctions=self.sanctions() if self.infra.sessions is not None else None,
        )

    def projections(self, session: AsyncSession) -> FriendsProjectionService:
        """Проекции друзей из событий в транзакции consumer."""
        return FriendsProjectionService(self.uow_factory(session), self.infra.clock)

    @property
    def config_commands(self) -> ConfigCommands:
        """Мутации версий конфига (для admin-API, поток F4)."""
        return ConfigCommands(self.uow_factory(), self.infra.clock, validate_config_body)

    @property
    def config_queries(self) -> ConfigQueries:
        """Чтение версий конфига (для admin-API, поток F4)."""
        return ConfigQueries(self.uow_factory())


class _NoDatabaseConfig:
    """Нет БД: конфига нет, зависящие от него операции закрыты."""

    async def current(self) -> SocialConfig:
        """Всегда отказ."""
        raise ConfigUnavailableError("база данных не настроена")


_ATTR = "_social_container"


def container_for(infra: Infrastructure) -> SocialContainer:
    """Один контейнер на процесс."""
    existing = getattr(infra, _ATTR, None)
    if isinstance(existing, SocialContainer):
        return existing
    container = SocialContainer(infra)
    setattr(infra, _ATTR, container)
    return container
