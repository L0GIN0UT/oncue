"""Сборка use-cases match с адаптерами (composition): api, consumer, relay, rooms.

Адаптеры создаются лениво: экспорт OpenAPI и тесты собирают сервис без инфраструктуры;
недостающая зависимость проявляется 503 при первом обращении.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from functools import cached_property

import structlog
from redis.asyncio import Redis

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7
from gv_core.random import Random, SystemRandom
from gv_game_kernel import AssetPort, PluginRegistry, register_builtin, register_collection
from gv_service import Infrastructure, ServiceAuthNotConfiguredError, build_service_token_provider
from gv_text.ugc import UgcPolicy
from match_service.application.commands.rooms import RoomLimits, RoomService
from match_service.application.owner import OwnerSettings, RoomOwner
from match_service.application.ports import (
    Json,
    ModeSource,
    RateLimiter,
    RealtimePublisher,
    RoundPlanPort,
)
from match_service.application.queries.matches import MatchQueries, RatePlayer
from match_service.application.queries.rooms import QueryLimits, RoomQueries
from match_service.application.rules import RulesFactory
from match_service.infrastructure.breaker import CircuitBreaker
from match_service.infrastructure.clients import (
    GameClient,
    MediaLinks,
    TokenProvider,
    UnconfiguredTokens,
)
from match_service.infrastructure.db.repositories import SqlMatchRecords, SqlModeHeads, SqlUsers
from match_service.infrastructure.limits import RedisRateLimiter
from match_service.infrastructure.realtime import (
    CentrifugoPublisher,
    NullPublisher,
    SubscriptionTokenIssuer,
)
from match_service.infrastructure.rooms.runner import RoomsRunner, RunnerSettings
from match_service.infrastructure.rooms.snapshot import RedisIdempotentCreates, RedisRoomStore
from match_service.infrastructure.rooms.stream import (
    RedisCommandBus,
    RedisPlayerRooms,
    RedisPresence,
)
from match_service.settings import Settings

log = structlog.get_logger(__name__)

MS = 1000


def build_registry() -> PluginRegistry:
    """Реестр плагинов процесса: встроенные (включая ``ContestRule``) и ``collection``."""
    return register_collection(register_builtin(PluginRegistry()))


def _tokens(infra: Infrastructure, audience: str) -> TokenProvider:
    try:
        return build_service_token_provider(infra, audience)
    except ServiceAuthNotConfiguredError:
        log.warning("match.service_auth_not_configured", audience=audience)
        return UnconfiguredTokens()


class CatalogModes:
    """``ModeSource``: текущая версия — проекция ``mode_heads``, тело — internal API game."""

    def __init__(self, heads: SqlModeHeads, game: GameClient) -> None:
        self._heads = heads
        self._game = game

    async def current(self, key: str) -> int | None:
        """Текущая опубликованная версия."""
        return await self._heads.current(key)

    async def body(self, key: str, version: int) -> Json | None:
        """Тело версии."""
        return await self._game.body(key, version)


@dataclass
class MatchAdapters:
    """Подменяемые адаптеры (тесты: ``gv_testkit.match`` и свои фейки)."""

    registry: PluginRegistry = field(default_factory=build_registry)
    plans: RoundPlanPort | None = None
    assets: AssetPort | None = None
    publisher: RealtimePublisher | None = None
    modes: ModeSource | None = None
    limiter: RateLimiter | None = None
    random: Random = field(default_factory=SystemRandom)
    instance: str | None = None


class MatchContainer:
    """Use-cases match поверх инфраструктуры процесса."""

    def __init__(self, infra: Infrastructure, adapters: MatchAdapters | None = None) -> None:
        if not isinstance(infra.settings, Settings):
            raise TypeError("ожидаются настройки match")
        self.infra = infra
        self.settings: Settings = infra.settings
        self.adapters = adapters or MatchAdapters()

    @property
    def service_name(self) -> str:
        """Имя сервиса."""
        return self.settings.service_name

    # ------------------------------------------------------------------ инфраструктура
    @property
    def redis(self) -> Redis:
        """Redis комнат."""
        if self.infra.redis is None:
            raise DependencyUnavailableError("Redis не настроен (GV_REDIS_URL)")
        return self.infra.redis

    @cached_property
    def factory(self) -> RulesFactory:
        """Правила режимов."""
        return RulesFactory(self.adapters.registry)

    @cached_property
    def store(self) -> RedisRoomStore:
        """Снимки комнат."""
        return RedisRoomStore(
            self.redis, snapshot_ttl_ms=int(self.settings.match.snapshot_ttl_h * 3_600_000)
        )

    @cached_property
    def player_rooms(self) -> RedisPlayerRooms:
        """Активная комната игрока."""
        return RedisPlayerRooms(self.redis)

    @cached_property
    def bus(self) -> RedisCommandBus:
        """Поток команд."""
        return RedisCommandBus(self.redis, maxlen=self.settings.match.stream_maxlen)

    @cached_property
    def presence(self) -> RedisPresence:
        """Heartbeat."""
        return RedisPresence(self.redis, ttl_s=self._presence_ttl_s)

    @property
    def _presence_ttl_s(self) -> int:
        return int(self.settings.match.snapshot_ttl_h * 3600)

    @cached_property
    def limiter(self) -> RateLimiter:
        """Лимиты частоты."""
        return self.adapters.limiter or RedisRateLimiter(self.redis, self.infra.clock)

    @cached_property
    def records(self) -> SqlMatchRecords:
        """История матчей."""
        if self.infra.sessions is None:
            raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
        return SqlMatchRecords(self.infra.sessions, self.infra.clock)

    @cached_property
    def users(self) -> SqlUsers:
        """Ники."""
        return SqlUsers(self.infra.sessions)

    @cached_property
    def game(self) -> GameClient:
        """Клиент game."""
        m = self.settings.match
        client = GameClient(
            m.game_url,
            _tokens(self.infra, m.game_audience),
            timeout_s=m.game_timeout_s,
            breaker=CircuitBreaker(m.breaker_failures, m.breaker_cooldown_s),
        )
        self.infra.add_closer(client.aclose)
        return client

    @cached_property
    def plans(self) -> RoundPlanPort:
        """План раундов."""
        return self.adapters.plans or self.game

    @cached_property
    def assets(self) -> AssetPort:
        """Ссылки уровней."""
        if self.adapters.assets is not None:
            return self.adapters.assets
        m = self.settings.match
        client = MediaLinks(
            m.media_url,
            _tokens(self.infra, m.media_audience),
            timeout_s=m.media_timeout_s,
            breaker=CircuitBreaker(m.breaker_failures, m.breaker_cooldown_s),
        )
        self.infra.add_closer(client.aclose)
        return client

    @cached_property
    def modes(self) -> ModeSource:
        """Онлайн-шаблоны."""
        return self.adapters.modes or CatalogModes(SqlModeHeads(self.infra.sessions), self.game)

    @cached_property
    def publisher(self) -> RealtimePublisher:
        """Centrifugo (или выключен — HTTP-tick)."""
        if self.adapters.publisher is not None:
            return self.adapters.publisher
        realtime = self.settings.realtime
        if realtime is None or realtime.api_url is None or realtime.api_key is None:
            log.warning("match.realtime_not_configured")
            return NullPublisher()
        publisher = CentrifugoPublisher(realtime)
        self.infra.add_closer(publisher.aclose)
        return publisher

    @cached_property
    def tokens(self) -> SubscriptionTokenIssuer:
        """Subscription-токены."""
        return SubscriptionTokenIssuer(self.settings.realtime)

    @cached_property
    def ugc(self) -> UgcPolicy:
        """Denylist чата (ADR 0030)."""
        return UgcPolicy.from_file(self.settings.ugc.denylist_path)

    # ------------------------------------------------------------------ use-cases
    @cached_property
    def owner(self) -> RoomOwner:
        """Владелец комнат (процесс rooms)."""
        m = self.settings.match
        return RoomOwner(
            factory=self.factory,
            plans=self.plans,
            assets=self.assets,
            records=self.records,
            store=self.store,
            player_rooms=self.player_rooms,
            clock=self.infra.clock,
            random=self.adapters.random,
            new_id=uuid7,
            settings=OwnerSettings(
                op_window=m.op_window,
                memory_limit=m.memory_limit,
                idle_ms=int(m.room_idle_close_min * 60 * MS),
                presence_timeout_ms=m.presence_timeout_s * MS,
            ),
        )

    @cached_property
    def rooms(self) -> RoomService:
        """Команды комнат (api)."""
        m = self.settings.match
        return RoomService(
            store=self.store,
            bus=self.bus,
            player_rooms=self.player_rooms,
            modes=self.modes,
            factory=self.factory,
            users=self.users,
            limiter=self.limiter,
            creates=RedisIdempotentCreates(
                self.redis, ttl_s=int(self.settings.idempotency.retention_h * 3600)
            ),
            ugc=self.ugc,
            clock=self.infra.clock,
            random=self.adapters.random,
            limits=RoomLimits(
                reply_timeout_ms=m.reply_timeout_ms,
                create_per_hour=m.lobby_create_per_user_per_hour,
                join_by_code_per_ip_per_min=m.join_by_code_per_ip_per_min,
                commands_per_s=m.commands_per_player_per_s,
                chat_per_min=m.chat_per_player_per_min,
                chat_slow_interval_s=m.chat_slow_interval_s,
            ),
        )

    @cached_property
    def room_queries(self) -> RoomQueries:
        """Чтение комнат (api)."""
        m = self.settings.match
        return RoomQueries(
            store=self.store,
            presence=self.presence,
            tokens=self.tokens,
            limiter=self.limiter,
            clock=self.infra.clock,
            limits=QueryLimits(
                join_by_code_per_ip_per_min=m.join_by_code_per_ip_per_min,
                tokens_per_min=m.realtime_tokens_per_user_per_min,
            ),
        )

    @cached_property
    def matches(self) -> MatchQueries:
        """История матчей."""
        return MatchQueries(self.records)

    @cached_property
    def rate(self) -> RatePlayer:
        """Оценки игроков."""
        return RatePlayer(
            self.records,
            self.limiter,
            self.infra.clock,
            per_hour=self.settings.match.rate_per_user_per_hour,
        )

    def runner(self) -> RoomsRunner:
        """Runner процесса rooms."""
        m = self.settings.match
        instance = self.adapters.instance or f"{socket.gethostname()}-{uuid7().hex[-8:]}"
        return RoomsRunner(
            self.redis,
            owner=self.owner,
            publisher=self.publisher,
            store=self.store,
            clock=self.infra.clock,
            settings=RunnerSettings(
                instance=instance.replace("|", "-"),
                lease_ttl_ms=int(m.lease_ttl_s * MS),
                renew_ms=int(m.lease_renew_s * MS),
                poll_ms=m.rooms_poll_ms,
                max_rooms=m.max_rooms_per_instance,
                scan_ms=int(m.orphan_scan_s * MS),
                snapshot_ttl_ms=int(m.snapshot_ttl_h * 3_600_000),
                closed_ttl_s=m.closed_ttl_s,
                reply_ttl_s=m.reply_ttl_s,
                presence_ttl_s=self._presence_ttl_s,
            ),
        )


_ATTR = "_match_container"


def container_for(infra: Infrastructure, adapters: MatchAdapters | None = None) -> MatchContainer:
    """Один контейнер на процесс (компоненты ``all`` делят клиентов и кэши)."""
    existing = getattr(infra, _ATTR, None)
    if isinstance(existing, MatchContainer) and adapters in (None, existing.adapters):
        return existing
    container = MatchContainer(infra, adapters)
    setattr(infra, _ATTR, container)
    return container
