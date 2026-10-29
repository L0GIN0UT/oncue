"""Сборка use-cases game с адаптерами (composition): общая для API, consumer и relay.

Адаптеры создаются без подключений (экспорт OpenAPI и тесты собирают сервис без
инфраструктуры); недостающая зависимость проявляется 503 при первом обращении.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from game_service.application.artwork import ArtworkProfiles
from game_service.application.catalog import ModeCatalog
from game_service.application.commands.daily import (
    DailySetAdmin,
    DailySetGenerator,
    DailySettings,
    ScheduleDailySets,
)
from game_service.application.commands.modes import (
    BootstrapModes,
    ConfigLimits,
    ModeConfigCommands,
    ModeConfigQueries,
    RequestClueAssets,
    ValidateModeConfig,
)
from game_service.application.commands.play import PlayLimits, PlayService
from game_service.application.commands.projection import (
    ApplyAssetsEvent,
    ApplyCatalogEvent,
    ApplyCollectionEvent,
    ApplyLadderEvent,
    ForgetUser,
)
from game_service.application.ports import (
    ModeConfigSource,
    RateLimiter,
    TokenProvider,
    UnitOfWorkFactory,
)
from game_service.application.queries.play import PlayQueries
from game_service.application.queries.round_plans import RoundPlans
from game_service.application.rules import RulesFactory
from game_service.application.views import RevealLinkCache, SessionPresenter, ShareSettings
from game_service.infrastructure.config_cache import CachedModeConfigs
from game_service.infrastructure.db.repositories import SqlGameUnitOfWork
from game_service.infrastructure.limits import NoopRateLimiter, RedisRateLimiter
from game_service.infrastructure.media import (
    MediaClientSettings,
    MediaPipelineAssetClient,
    UnconfiguredTokenProvider,
)
from game_service.infrastructure.plugins import CatalogRegistry, build_registry
from game_service.settings import Settings
from gv_core.errors import DependencyUnavailableError
from gv_core.random import Random, SystemRandom
from gv_game_kernel import AssetPort
from gv_service import (
    Infrastructure,
    ServiceAuthNotConfiguredError,
    build_service_token_provider,
)

log = structlog.get_logger(__name__)


def default_token_provider(infra: Infrastructure, audience: str) -> TokenProvider:
    """Сервисный токен для media-pipeline (D13).

    Провайдер собирает ``gv_service.build_service_token_provider`` по общим
    ``GV_SERVICE_AUTH__*``; без учётных данных выдача ссылок отвечает 503.
    """
    try:
        return build_service_token_provider(infra, audience)
    except ServiceAuthNotConfiguredError:
        log.warning("game.service_auth_not_configured")
        return UnconfiguredTokenProvider()


@dataclass
class GameAdapters:
    """Адаптеры портов. Тесты подменяют поля (реестр плагинов, ссылки, случайность)."""

    registry: CatalogRegistry
    assets: AssetPort
    limiter: RateLimiter
    random: Random

    @classmethod
    def from_settings(cls, infra: Infrastructure, settings: Settings) -> GameAdapters:
        """Боевые адаптеры."""
        g = settings.game
        client = MediaPipelineAssetClient(
            MediaClientSettings(
                base_url=g.media_url,
                timeout_s=g.media_timeout_s,
                retries=g.media_retries,
                breaker_failures=g.media_breaker_failures,
                breaker_cooldown_s=g.media_breaker_cooldown_s,
            ),
            default_token_provider(infra, g.media_audience),
        )
        infra.add_closer(client.aclose)
        limiter: RateLimiter = (
            RedisRateLimiter(infra.redis, infra.clock) if infra.redis else NoopRateLimiter()
        )
        return cls(registry=build_registry(), assets=client, limiter=limiter, random=SystemRandom())


class GameContainer:
    """Use-cases сервиса поверх инфраструктуры процесса."""

    def __init__(self, infra: Infrastructure, adapters: GameAdapters | None = None) -> None:
        if not isinstance(infra.settings, Settings):
            raise TypeError("ожидаются настройки game")
        self.infra = infra
        self.settings: Settings = infra.settings
        self.adapters = adapters or GameAdapters.from_settings(infra, self.settings)

    def uow_factory(self, session: AsyncSession | None = None) -> UnitOfWorkFactory:
        """Фабрика UoW: своя транзакция или SAVEPOINT в транзакции consumer."""
        infra = self.infra
        producer = self.settings.service_name
        limit = self.settings.game.candidate_limit

        def factory() -> SqlGameUnitOfWork:
            if session is not None:
                return SqlGameUnitOfWork(
                    producer=producer, clock=infra.clock, candidate_limit=limit, session=session
                )
            if infra.sessions is None:
                raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
            return SqlGameUnitOfWork(
                producer=producer, clock=infra.clock, candidate_limit=limit, sessions=infra.sessions
            )

        return factory

    # ------------------------------------------------------------------ общее
    @cached_property
    def factory(self) -> RulesFactory:
        """Сборка правил режимов."""
        return RulesFactory(self.adapters.registry)

    @cached_property
    def configs(self) -> ModeConfigSource:
        """Кэш конфигов."""
        return CachedModeConfigs(
            self.uow_factory(), self.infra.redis, ttl_s=self.settings.game.config_cache_ttl_s
        )

    @cached_property
    def catalog(self) -> ModeCatalog:
        """Каталог режимов."""
        return ModeCatalog(self.configs, self.factory)

    @cached_property
    def validator(self) -> ValidateModeConfig:
        """Проверка конфигов."""
        return ValidateModeConfig(
            self.factory, ConfigLimits(self.settings.game.mode_config_max_bytes)
        )

    @cached_property
    def daily_settings(self) -> DailySettings:
        """Параметры наборов дня."""
        g = self.settings.game
        secret = g.daily_seed_secret.get_secret_value().encode() if g.daily_seed_secret else None
        return DailySettings(
            seed_secret=secret,
            candidate_limit=g.candidate_limit,
            admin_range_days=g.admin_daily_range_days,
        )

    @cached_property
    def generator(self) -> DailySetGenerator:
        """Розыгрыш наборов дня."""
        return DailySetGenerator(self.daily_settings)

    @cached_property
    def presenter(self) -> SessionPresenter:
        """JSON сессий."""
        g = self.settings.game
        return SessionPresenter(
            self.adapters.assets,
            RevealLinkCache(
                self.infra.clock, margin_s=g.reveal_link_margin_s, max_size=g.reveal_cache_size
            ),
            ShareSettings(title=g.share_title, url=g.share_url),
        )

    # ------------------------------------------------------------------ use-cases
    @cached_property
    def mode_commands(self) -> ModeConfigCommands:
        """Мутации конфигов."""
        return ModeConfigCommands(
            self.uow_factory(), self.infra.clock, self.validator, self.configs
        )

    @cached_property
    def mode_queries(self) -> ModeConfigQueries:
        """Чтение конфигов (админка)."""
        return ModeConfigQueries(self.uow_factory(), self.validator)

    @cached_property
    def bootstrap(self) -> BootstrapModes:
        """Публикация сидов."""
        return BootstrapModes(self.uow_factory(), self.infra.clock, self.validator, self.configs)

    @cached_property
    def schedule(self) -> ScheduleDailySets:
        """Планировщик наборов дня."""
        return ScheduleDailySets(self.uow_factory(), self.infra.clock, self.catalog, self.generator)

    @cached_property
    def daily_admin(self) -> DailySetAdmin:
        """Админка наборов дня."""
        return DailySetAdmin(
            self.uow_factory(), self.infra.clock, self.catalog, self.generator, self.daily_settings
        )

    @cached_property
    def play(self) -> PlayService:
        """Игровой цикл."""
        g, lim = self.settings.game, self.settings.game_limits
        return PlayService(
            uow=self.uow_factory(),
            clock=self.infra.clock,
            random=self.adapters.random,
            catalog=self.catalog,
            daily=self.schedule,
            assets=self.adapters.assets,
            limiter=self.adapters.limiter,
            presenter=self.presenter,
            artwork=ArtworkProfiles(self.settings.game.artwork_profiles),
            limits=PlayLimits(
                guess_per_min=lim.guess_per_min,
                clue_per_min=lim.clue_per_min,
                sessions_per_hour=lim.sessions_per_hour,
                guest_sessions_per_ip_per_hour=lim.guest_sessions_per_ip_per_hour,
                fast_exact_ms=g.fast_exact_ms,
                candidate_limit=g.candidate_limit,
                final_clue_per_min=lim.final_clue_per_min,
            ),
        )

    @cached_property
    def queries(self) -> PlayQueries:
        """Запросы публичного API."""
        return PlayQueries(
            self.uow_factory(),
            self.infra.clock,
            self.catalog,
            self.presenter,
            self.adapters.limiter,
            subjects_per_min=self.settings.game_limits.subjects_per_min,
            playability_per_min=self.settings.game_limits.playability_per_min,
            candidate_limit=self.settings.game.candidate_limit,
        )

    @cached_property
    def round_plans(self) -> RoundPlans:
        """План раундов матча для match (internal API, ADR 0033)."""
        return RoundPlans(
            self.uow_factory(), self.catalog, candidate_limit=self.settings.game.candidate_limit
        )

    @cached_property
    def asset_requests(self) -> RequestClueAssets:
        """Запросы ассетов под загадки черновиков (ADR 0025)."""
        return RequestClueAssets(self.uow_factory(), self.infra.clock, self.factory)

    catalog_events = ApplyCatalogEvent()
    assets_events = ApplyAssetsEvent()
    collection_events = ApplyCollectionEvent()
    ladder_events = ApplyLadderEvent()
    forget_user = ForgetUser()


_ATTR = "_game_container"


def container_for(infra: Infrastructure, adapters: GameAdapters | None = None) -> GameContainer:
    """Один контейнер на процесс (api, consumer и relay в ``all`` делят кэши и клиентов).

    Явные ``adapters`` (тесты) заменяют контейнер процесса, если он собран с другими
    адаптерами; повторный вызов с теми же адаптерами возвращает тот же контейнер.
    """
    existing = getattr(infra, _ATTR, None)
    if isinstance(existing, GameContainer) and adapters in (None, existing.adapters):
        return existing
    container = GameContainer(infra, adapters)
    setattr(infra, _ATTR, container)
    return container
