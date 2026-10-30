"""Сборка use-cases match с адаптерами (composition): api, consumer, relay, rooms.

Адаптеры создаются лениво: экспорт OpenAPI и тесты собирают сервис без инфраструктуры;
недостающая зависимость проявляется 503 при первом обращении.
"""

from __future__ import annotations

import secrets
import socket
from dataclasses import dataclass, field
from functools import cached_property

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7
from gv_core.random import Random, SystemRandom
from gv_game_kernel import AssetPort, PluginRegistry, register_builtin, register_collection
from gv_service import Infrastructure, ServiceAuthNotConfiguredError, build_service_token_provider
from gv_text.ugc import UgcPolicy
from match_service.application.commands.queue import QueueLimits, QueueService
from match_service.application.commands.rooms import RoomLimits, RoomService
from match_service.application.matchmaking import (
    MatcherSettings,
    Matchmaker,
    QueueHooks,
    RankedTemplates,
)
from match_service.application.owner import OwnerSettings, RoomOwner, is_transient
from match_service.application.ports import (
    Json,
    LadderProjection,
    ModeKeys,
    ModeSource,
    RateLimiter,
    RealtimePublisher,
    RoundPlanPort,
    Subscriptions,
)
from match_service.application.queries.matches import MatchQueries, RatePlayer
from match_service.application.queries.rooms import QueryLimits, RoomQueries
from match_service.application.rules import RulesFactory
from match_service.application.sanctions import SanctionGate, SanctionsProjection
from match_service.infrastructure.breaker import CircuitBreaker
from match_service.infrastructure.clients import (
    GameClient,
    MediaLinks,
    TokenProvider,
    UnconfiguredTokens,
)
from match_service.infrastructure.db.ladders import SqlLadders
from match_service.infrastructure.db.repositories import (
    SqlAdminAudit,
    SqlMatchRecords,
    SqlModeHeads,
    SqlUsers,
)
from match_service.infrastructure.db.sanctions import SANCTIONS_UNAVAILABLE, SqlSanctions
from match_service.infrastructure.ladder_cache import CachedLadders
from match_service.infrastructure.limits import RedisRateLimiter
from match_service.infrastructure.matchmaker import MatchmakerRunner
from match_service.infrastructure.queue import RedisQueue
from match_service.infrastructure.realtime import (
    CentrifugoPublisher,
    NullPublisher,
    SubscriptionTokenIssuer,
)
from match_service.infrastructure.revocation import DirectRevoker, RevocationQueue
from match_service.infrastructure.rooms.runner import RoomsRunner, RunnerSettings
from match_service.infrastructure.rooms.snapshot import (
    RedisIdempotentCreates,
    RedisRoomStore,
    RedisSpectators,
)
from match_service.infrastructure.rooms.stream import (
    RedisCommandBus,
    RedisPlayerRooms,
    RedisPresence,
)
from match_service.settings import Settings

log = structlog.get_logger(__name__)

MS = 1000


def _transient(exc: BaseException) -> bool:
    """Сбой Redis или сети: команду владелец не пропускает, а повторяет после перечитывания."""
    return is_transient(exc) or isinstance(exc, RedisError)


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
    subscriptions: Subscriptions | None = None
    modes: ModeSource | None = None
    mode_keys: ModeKeys | None = None
    ladders: LadderProjection | None = None
    sanctions: SanctionsProjection | None = None
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
        # Указатель живёт не дольше снимка комнаты.
        ttl_ms = int(self.settings.match.snapshot_ttl_h * 3600 * 1000)
        return RedisPlayerRooms(self.redis, ttl_ms=ttl_ms)

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
    def spectators(self) -> RedisSpectators:
        """Наблюдатели комнат (отзыв подписок по окончании матча)."""
        return RedisSpectators(self.redis)

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
    def sanction_gate(self) -> SanctionGate | None:
        """Эффективные санкции игрока (проекция ``social``, ADR 0052); без БД - только claim."""
        projection = self.adapters.sanctions
        if projection is None:
            if self.infra.sessions is None:
                return None
            projection = SqlSanctions(self.infra.sessions)
        return SanctionGate(projection, self.infra.clock, on_failure=SANCTIONS_UNAVAILABLE.inc)

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
    def ladders(self) -> CachedLadders:
        """Проекция ladder-ов и рейтингов (из ``rating.rating.v1``), с кэшем процесса."""
        inner = self.adapters.ladders or SqlLadders(
            self.infra.sessions, self.settings.match.default_start_rating
        )
        return CachedLadders(inner, self.settings.match.ladder_cache_s)

    @cached_property
    def ranked_queue(self) -> RedisQueue:
        """Очереди подбора (Redis)."""
        m = self.settings.match
        return RedisQueue(
            self.redis,
            ttl_ms=int(m.queue_ttl_s * MS),
            matched_ttl_ms=int(m.queue_matched_ttl_s * MS),
            reserved_ttl_ms=int(m.queue_reserved_ttl_s * MS),
        )

    @cached_property
    def ranked_templates(self) -> RankedTemplates:
        """Рейтинговые шаблоны очередей (дуэли ladder-а)."""
        heads = self.adapters.mode_keys or SqlModeHeads(self.infra.sessions)
        return RankedTemplates(heads, self.modes, self.factory)

    @cached_property
    def publisher(self) -> RealtimePublisher:
        """Centrifugo (или выключен — HTTP-tick)."""
        if self.adapters.publisher is not None:
            return self.adapters.publisher
        realtime = self.settings.realtime
        if realtime is None or realtime.api_url is None or realtime.api_key is None:
            log.warning("match.realtime_not_configured")
            return NullPublisher()
        publisher = CentrifugoPublisher(
            realtime,
            unsubscribe_attempts=self.settings.match.unsubscribe_attempts,
            unsubscribe_retry_s=self.settings.match.unsubscribe_retry_s,
        )
        self.infra.add_closer(publisher.aclose)
        return publisher

    @cached_property
    def subscriptions(self) -> Subscriptions:
        """Отзыв подписок (server API Centrifugo или выключен)."""
        if self.adapters.subscriptions is not None:
            return self.adapters.subscriptions
        publisher = self.publisher
        if isinstance(publisher, CentrifugoPublisher):
            # Повторы с паузой - в фоне (ADR 0063): владелец комнаты Centrifugo не ждёт.
            m = self.settings.match
            queue = RevocationQueue(
                publisher,
                workers=m.revocation_workers,
                capacity=m.revocation_queue_size,
                drain_s=m.revocation_drain_s,
            )
            # Закрытие LIFO: очередь допускается до закрытия HTTP-клиента публикатора.
            self.infra.add_closer(queue.aclose)
            return queue
        return NullPublisher()

    @cached_property
    def ban_revoker(self) -> DirectRevoker:
        """Синхронный отзыв соединений при бане аккаунта (мимо очереди, ADR 0059/0063)."""
        if self.adapters.subscriptions is not None:
            return DirectRevoker(self.adapters.subscriptions)
        publisher = self.publisher
        if isinstance(publisher, CentrifugoPublisher):
            return DirectRevoker(publisher)
        return DirectRevoker(NullPublisher())

    @cached_property
    def audit(self) -> SqlAdminAudit:
        """Аудит админских действий."""
        if self.infra.sessions is None:
            raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
        return SqlAdminAudit(self.infra.sessions)

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
            # Seed плана — криптостойкий секрет только в снимке (аудит, блокер 1).
            seeds=lambda: secrets.token_hex(32),
            subscriptions=self.subscriptions,
            settings=OwnerSettings(
                op_window=m.op_window,
                memory_limit=m.memory_limit,
                idle_ms=int(m.room_idle_close_min * 60 * MS),
                presence_timeout_ms=m.presence_timeout_s * MS,
                poison_limit=m.poison_limit,
                op_users_max=m.op_users_max,
                pending_room_ms=m.pending_room_ms,
                empty_lobby_ms=int(m.empty_lobby_close_s * MS),
            ),
            transient=_transient,
            ranked=QueueHooks(self.ranked_queue),
            spectators=self.spectators,
        )

    @cached_property
    def queue(self) -> QueueService:
        """Очередь рейтинговой дуэли (api)."""
        m = self.settings.match
        return QueueService(
            queue=self.ranked_queue,
            ladders=self.ladders,
            templates=self.ranked_templates,
            player_rooms=self.player_rooms,
            store=self.store,
            bus=self.bus,
            presence=self.presence,
            limiter=self.limiter,
            creates=RedisIdempotentCreates(
                self.redis, ttl_s=int(self.settings.idempotency.retention_h * 3600)
            ),
            clock=self.infra.clock,
            limits=QueueLimits(
                tick_s=m.tick_s,
                enter_per_min=m.queue_enter_per_user_per_min,
                tick_per_min=m.queue_tick_per_user_per_min,
                pending_room_ms=m.pending_room_ms,
                window_s=m.rate_window_s,
            ),
            sanctions=self.sanction_gate,
        )

    @cached_property
    def matchmaker(self) -> Matchmaker:
        """Батчевый матчер очередей (процесс rooms)."""
        m = self.settings.match
        return Matchmaker(
            queue=self.ranked_queue,
            templates=self.ranked_templates,
            player_rooms=self.player_rooms,
            store=self.store,
            bus=self.bus,
            users=self.users,
            clock=self.infra.clock,
            random=self.adapters.random,
            settings=MatcherSettings(
                batch_max=m.queue_batch_max,
                pending_room_ms=m.pending_room_ms,
                reserved_stale_ms=int(m.queue_reserved_stale_s * MS),
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
                join_by_code_per_user_per_min=m.join_by_code_per_user_per_min,
                emote_per_min=m.emote_per_player_per_min,
                create_lock_ms=m.create_lock_ms,
                pending_room_ms=m.pending_room_ms,
                room_name_max_length=m.room_name_max_length,
            ),
            audit=self.audit if self.infra.sessions is not None else None,
            sanctions=self.sanction_gate,
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
                join_by_code_per_user_per_min=m.join_by_code_per_user_per_min,
                tick_per_min=m.tick_per_player_per_min,
                state_per_min=m.state_per_player_per_min,
                spectate_per_min=m.spectate_per_user_per_min,
                spectate_scan_pages=m.spectate_scan_pages,
                window_s=m.rate_window_s,
                spectator_ttl_s=int(m.snapshot_ttl_h * 3600),
            ),
            spectators=self.spectators,
            sanctions=self.sanction_gate,
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

    def matchmaker_runner(self) -> MatchmakerRunner:
        """Runner матчера очередей (рядом с runner-ом комнат)."""
        m = self.settings.match
        instance = self.adapters.instance or f"{socket.gethostname()}-{uuid7().hex[-8:]}"
        return MatchmakerRunner(
            self.redis,
            matchmaker=self.matchmaker,
            ladders=self.ladders,
            instance=instance.replace("|", "-"),
            lease_ttl_ms=int(m.matcher_lease_ttl_s * MS),
            poll_ms=m.matcher_poll_ms,
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
                reader_backoff_max_s=m.command_reader_backoff_max_s,
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
