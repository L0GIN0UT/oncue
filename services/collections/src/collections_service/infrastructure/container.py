"""Сборка use-cases collections с адаптерами: общая для API, consumer и relay.

Адаптеры создаются без подключений (экспорт OpenAPI и тесты собирают сервис без
инфраструктуры); недостающая БД проявляется 503 при первом обращении.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from sqlalchemy.ext.asyncio import AsyncSession

from collections_service.application.commands.admin import AdminCommands
from collections_service.application.commands.author import AuthorCommands, AuthorLimits
from collections_service.application.commands.projection import (
    CatalogEvents,
    Completions,
    ModerationEvents,
    PopularRecompute,
    SweepBlockedItems,
    UserEvents,
)
from collections_service.application.commands.reactions import ReactionCommands, ReactionLimits
from collections_service.application.ports import RateLimiter, UnitOfWorkFactory
from collections_service.application.queries.collections import CollectionQueries
from collections_service.application.views import Presenter
from collections_service.domain.difficulty import DifficultyModel
from collections_service.domain.policy import CollectionPolicy, Limits, MetaConfig
from collections_service.infrastructure.db.repositories import SqlCollectionsUnitOfWork
from collections_service.infrastructure.limits import RedisRateLimiter, UnconfiguredRateLimiter
from collections_service.infrastructure.meta import load_meta
from collections_service.settings import Settings
from gv_core.errors import DependencyUnavailableError
from gv_service import Infrastructure
from gv_text.ugc import UgcPolicy


@dataclass
class CollectionsAdapters:
    """Адаптеры портов (тесты подменяют лимитер)."""

    limiter: RateLimiter

    @classmethod
    def from_infra(cls, infra: Infrastructure) -> CollectionsAdapters:
        """Боевые адаптеры."""
        limiter: RateLimiter = (
            RedisRateLimiter(infra.redis, infra.clock) if infra.redis else UnconfiguredRateLimiter()
        )
        return cls(limiter=limiter)


class CollectionsContainer:
    """Use-cases сервиса поверх инфраструктуры процесса."""

    def __init__(self, infra: Infrastructure, adapters: CollectionsAdapters | None = None) -> None:
        if not isinstance(infra.settings, Settings):
            raise TypeError("ожидаются настройки collections")
        self.infra = infra
        self.settings: Settings = infra.settings
        self.adapters = adapters or CollectionsAdapters.from_infra(infra)

    # ------------------------------------------------------------------ общее
    @cached_property
    def meta(self) -> MetaConfig:
        """Словари конструктора."""
        return load_meta(self.settings.collections.meta_config)

    @cached_property
    def difficulty(self) -> DifficultyModel:
        """Формула сложности."""
        d = self.settings.collections.difficulty
        return DifficultyModel(prior=d.prior, m=d.m, min_plays=d.min_plays, thresholds=d.thresholds)

    @cached_property
    def policy(self) -> CollectionPolicy:
        """Проверки содержимого."""
        lim = self.settings.collections.limits
        return CollectionPolicy(
            Limits(
                title_max=lim.title_max,
                description_max=lim.description_max,
                items_min=lim.items_min,
                items_max=lim.items_max,
                tags_max=lim.tags_max,
                emoji_max_graphemes=lim.emoji_max_graphemes,
                emoji_min_graphemes=lim.emoji_min_graphemes,
            ),
            UgcPolicy.from_file(self.settings.ugc.denylist_path),
            self.meta,
        )

    @cached_property
    def presenter(self) -> Presenter:
        """JSON."""
        return Presenter(self.difficulty)

    def uow_factory(self, session: AsyncSession | None = None) -> UnitOfWorkFactory:
        """Фабрика UoW: своя транзакция или SAVEPOINT в транзакции consumer."""
        infra = self.infra
        producer = self.settings.service_name
        terms = self.meta.search_terms

        def factory() -> SqlCollectionsUnitOfWork:
            if session is not None:
                return SqlCollectionsUnitOfWork(
                    producer=producer, clock=infra.clock, tag_terms=terms, session=session
                )
            if infra.sessions is None:
                raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
            return SqlCollectionsUnitOfWork(
                producer=producer, clock=infra.clock, tag_terms=terms, sessions=infra.sessions
            )

        return factory

    # ------------------------------------------------------------------ use-cases
    @cached_property
    def author(self) -> AuthorCommands:
        """Мутации автора."""
        lim = self.settings.collections.limits
        return AuthorCommands(
            self.uow_factory(),
            self.infra.clock,
            policy=self.policy,
            limiter=self.adapters.limiter,
            limits=AuthorLimits(drafts_max=lim.drafts_max, publish_per_day=lim.publish_per_day),
            presenter=self.presenter,
        )

    @cached_property
    def reactions(self) -> ReactionCommands:
        """Лайки и избранное."""
        return ReactionCommands(
            self.uow_factory(),
            self.infra.clock,
            self.adapters.limiter,
            ReactionLimits(per_min=self.settings.collections.limits.reactions_per_min),
        )

    @cached_property
    def admin(self) -> AdminCommands:
        """Модерация и кураторство."""
        return AdminCommands(self.uow_factory(), self.infra.clock, self.policy, self.presenter)

    @cached_property
    def queries(self) -> CollectionQueries:
        """Чтение."""
        return CollectionQueries(
            self.uow_factory(),
            self.presenter,
            self.difficulty,
            self.meta,
            self.adapters.limiter,
            search_per_min=self.settings.collections.limits.search_per_min,
        )

    @cached_property
    def catalog_events(self) -> CatalogEvents:
        """Проекция каталога."""
        return CatalogEvents(self.infra.clock, self.settings.collections.sweep.batch)

    @cached_property
    def sweep(self) -> SweepBlockedItems:
        """Остаток заблокированных единиц."""
        return SweepBlockedItems(
            self.uow_factory(), self.catalog_events, self.settings.collections.sweep.batch
        )

    @cached_property
    def completions(self) -> Completions:
        """«Пройден» и сложность."""
        return Completions(self.difficulty)

    @cached_property
    def users(self) -> UserEvents:
        """События identity."""
        return UserEvents(self.infra.clock)

    @cached_property
    def moderation(self) -> ModerationEvents:
        """Решения модерации social."""
        return ModerationEvents(self.infra.clock)

    @cached_property
    def popular(self) -> PopularRecompute:
        """Пересчёт популярности."""
        p = self.settings.collections.popular
        return PopularRecompute(
            self.uow_factory(),
            self.infra.clock,
            window_days=p.window_days,
            like_weight=p.like_weight,
            play_weight=p.play_weight,
            batch=p.batch,
            retention_days=p.stats_retention_days,
        )


_ATTR = "_collections_container"


def container_for(
    infra: Infrastructure, adapters: CollectionsAdapters | None = None
) -> CollectionsContainer:
    """Один контейнер на процесс (api, consumer и relay в ``all`` делят кэши)."""
    existing = getattr(infra, _ATTR, None)
    if isinstance(existing, CollectionsContainer) and adapters in (None, existing.adapters):
        return existing
    container = CollectionsContainer(infra, adapters)
    setattr(infra, _ATTR, container)
    return container
