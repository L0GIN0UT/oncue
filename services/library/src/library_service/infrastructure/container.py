"""Сборка use-cases library с адаптерами (composition): общая для API, consumer и relay.

Адаптеры создаются без подключений (экспорт OpenAPI и тесты собирают сервис без
инфраструктуры); недостающая база проявляется 503 при первом обращении. Конфиг полок и
denylist читаются при сборке контейнера: ошибка конфигурации видна на старте процесса.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from sqlalchemy.ext.asyncio import AsyncSession

from gv_core.errors import DependencyUnavailableError
from gv_service import Infrastructure
from gv_text.ugc import UgcPolicy
from library_service.application.commands.projection import ApplyCatalogEvent, PurgeUser
from library_service.application.commands.save import CommandLimits, LibraryCommands
from library_service.application.ports import RateLimiter, TitlePolicy, UnitOfWorkFactory
from library_service.application.queries import LibraryQueries
from library_service.domain.library import LibraryLimits
from library_service.domain.shelves import ShelfCatalog
from library_service.infrastructure.db.repositories import SqlLibraryUnitOfWork
from library_service.infrastructure.limits import NoopRateLimiter, RedisRateLimiter
from library_service.infrastructure.shelves_config import load_shelf_catalog
from library_service.settings import Settings


@dataclass
class LibraryAdapters:
    """Адаптеры портов. Тесты подменяют поля (конфиг полок, лимитер, denylist)."""

    catalog: ShelfCatalog
    limiter: RateLimiter
    policy: TitlePolicy

    @classmethod
    def from_settings(cls, infra: Infrastructure, settings: Settings) -> LibraryAdapters:
        """Боевые адаптеры."""
        limiter: RateLimiter = (
            RedisRateLimiter(infra.redis, infra.clock) if infra.redis else NoopRateLimiter()
        )
        return cls(
            catalog=load_shelf_catalog(settings.library.shelves_config),
            limiter=limiter,
            policy=UgcPolicy.from_file(settings.ugc.denylist_path),
        )


class LibraryContainer:
    """Use-cases сервиса поверх инфраструктуры процесса."""

    def __init__(self, infra: Infrastructure, adapters: LibraryAdapters | None = None) -> None:
        if not isinstance(infra.settings, Settings):
            raise TypeError("ожидаются настройки library")
        self.infra = infra
        self.settings: Settings = infra.settings
        self.adapters = adapters or LibraryAdapters.from_settings(infra, self.settings)

    def uow_factory(self, session: AsyncSession | None = None) -> UnitOfWorkFactory:
        """Фабрика UoW: своя транзакция или SAVEPOINT в транзакции consumer."""
        infra = self.infra
        producer = self.settings.service_name

        def factory() -> SqlLibraryUnitOfWork:
            if session is not None:
                return SqlLibraryUnitOfWork(producer=producer, clock=infra.clock, session=session)
            if infra.sessions is None:
                raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
            return SqlLibraryUnitOfWork(
                producer=producer, clock=infra.clock, sessions=infra.sessions
            )

        return factory

    @cached_property
    def limits(self) -> LibraryLimits:
        """Лимиты агрегата."""
        lim = self.settings.library.limits
        return LibraryLimits(
            custom_shelves_max=lim.custom_shelves_max,
            items_per_shelf_max=lim.items_per_shelf_max,
        )

    @cached_property
    def commands(self) -> LibraryCommands:
        """Мутации."""
        lim = self.settings.library.limits
        return LibraryCommands(
            self.uow_factory(),
            self.infra.clock,
            catalog=self.adapters.catalog,
            limiter=self.adapters.limiter,
            policy=self.adapters.policy,
            limits=CommandLimits(
                saves_per_min=lim.saves_per_min,
                shelf_title_max=lim.shelf_title_max,
                library=self.limits,
            ),
        )

    @cached_property
    def queries(self) -> LibraryQueries:
        """Чтение."""
        return LibraryQueries(
            self.uow_factory(),
            self.adapters.catalog,
            self.limits,
            batch_max=self.settings.library.item_shelves_batch_max,
        )

    @cached_property
    def catalog_events(self) -> ApplyCatalogEvent:
        """Проекция catalog."""
        return ApplyCatalogEvent(self.infra.clock)

    @cached_property
    def purge(self) -> PurgeUser:
        """Удаление медиатеки пользователя."""
        lib = self.settings.library
        return PurgeUser(
            self.uow_factory(),
            self.infra.clock,
            batch=lib.purge_batch,
            users_per_run=lib.purge_users_per_run,
        )


_ATTR = "_library_container"


def container_for(
    infra: Infrastructure, adapters: LibraryAdapters | None = None
) -> LibraryContainer:
    """Один контейнер на процесс (api, consumer и relay в ``all`` делят адаптеры).

    Явные ``adapters`` (тесты) заменяют контейнер процесса, если он собран с другими.
    """
    existing = getattr(infra, _ATTR, None)
    if isinstance(existing, LibraryContainer) and adapters in (None, existing.adapters):
        return existing
    container = LibraryContainer(infra, adapters)
    setattr(infra, _ATTR, container)
    return container
