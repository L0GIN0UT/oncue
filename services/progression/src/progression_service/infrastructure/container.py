"""Сборка use-cases progression (composition): общая для API, consumer и relay."""

from __future__ import annotations

from functools import cached_property
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from gv_auth.service import ServiceTokenProvider
from gv_core.errors import DependencyUnavailableError
from gv_core.random import Random, SystemRandom
from gv_service import Infrastructure, build_service_token_provider
from progression_service.application.accrual.daily import AccrueDaily
from progression_service.application.accrual.match import AccrueMatch
from progression_service.application.accrual.rating_rewards import AccrueSeason, AccrueWeeklyTop
from progression_service.application.accrual.solo import AccrueSolo
from progression_service.application.admin import AdminServices
from progression_service.application.admin.configs import ConfigCommands, ConfigQueries
from progression_service.application.admin.events import EventAdminCommands, EventAdminQueries
from progression_service.application.admin.ledger import LedgerCommands, LedgerQueries
from progression_service.application.admin.ports import AdminUowFactory
from progression_service.application.admin.shop import (
    ItemCommands,
    ItemQueries,
    PackCommands,
    PackQueries,
    PromoCommands,
    PromoQueries,
)
from progression_service.application.appearance import AppearanceService
from progression_service.application.avatar import AvatarService
from progression_service.application.badges import BadgeQueries
from progression_service.application.boosts import BoostRewardHook
from progression_service.application.events.player import EventPlayerService
from progression_service.application.events.tick import EventTicker
from progression_service.application.inventory import InventoryService
from progression_service.application.ports import RewardHook, UnitOfWorkFactory
from progression_service.application.ports.avatar import MediaUgcClient, UgcUpload
from progression_service.application.ports.shop import AttemptLimiter, AttemptWindow
from progression_service.application.promo import PromoService
from progression_service.application.queries import ProgressQueries
from progression_service.application.quests import QuestQueries
from progression_service.application.quests.tracker import ProgressTracker
from progression_service.application.shop import ShopService, ShopServices
from progression_service.application.wheel import WheelService
from progression_service.infrastructure.adapters import (
    LazyCodeHasher,
    PillowMediaInspector,
    RedisAttemptLimiter,
    SecretsTokenSource,
    UnconfiguredLimiter,
    hmac_key,
)
from progression_service.infrastructure.db.admin_repositories import SqlAdminUnitOfWork
from progression_service.infrastructure.db.repositories import SqlProgressionUnitOfWork
from progression_service.infrastructure.media_client import HttpMediaUgcClient
from progression_service.infrastructure.storage import S3MediaStore
from progression_service.settings import Settings


class _LateMedia:
    """Клиент media-pipeline, выбираемый при каждом вызове: use-cases собираются вместе с
    роутерами, а тесты подменяют ``container.media_ugc`` уже после сборки."""

    def __init__(self, container: ProgressionContainer) -> None:
        self._container = container

    async def submit(
        self, *, owner_user_id: UUID, purpose: str, data: bytes, idempotency_key: str
    ) -> UgcUpload:
        return await self._container.media_ugc.submit(
            owner_user_id=owner_user_id,
            purpose=purpose,
            data=data,
            idempotency_key=idempotency_key,
        )


class _LateLimiter:
    """Лимитер, выбираемый при каждом вызове (как ``_LateMedia``)."""

    def __init__(self, container: ProgressionContainer) -> None:
        self._container = container

    async def hit(self, key: str, window_s: int) -> AttemptWindow:
        return await self._container.limiter.hit(key, window_s)


class ProgressionContainer:
    """Use-cases сервиса поверх инфраструктуры процесса."""

    def __init__(
        self, infra: Infrastructure, hook: RewardHook | None = None, rng: Random | None = None
    ) -> None:
        if not isinstance(infra.settings, Settings):
            raise TypeError("ожидаются настройки progression")
        self.infra = infra
        self.settings: Settings = infra.settings
        # Бусты опыта (P2) подключены через порт, ядро начисления о них не знает.
        self.reward_hook: RewardHook = hook or BoostRewardHook()
        # Сектор колеса выбирается криптостойким источником; тесты подставляют детерминированный.
        self.random: Random = rng or SystemRandom()
        # Коды промокодов - отдельный криптостойкий источник: тесты колеса подменяют ``random``.
        self.code_random: Random = SystemRandom()

    def uow_factory(self, session: AsyncSession | None = None) -> UnitOfWorkFactory:
        """Фабрика UoW: своя транзакция или SAVEPOINT в транзакции consumer."""
        infra = self.infra
        producer = self.settings.service_name

        def factory() -> SqlProgressionUnitOfWork:
            if session is not None:
                return SqlProgressionUnitOfWork(
                    producer=producer, clock=infra.clock, session=session
                )
            if infra.sessions is None:
                raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
            return SqlProgressionUnitOfWork(
                producer=producer, clock=infra.clock, sessions=infra.sessions
            )

        return factory

    # ----- начисления в транзакции consumer
    def accrue_solo(self, session: AsyncSession) -> AccrueSolo:
        """Соло-сессия."""
        return AccrueSolo(self.uow_factory(session), self.infra.clock)

    def accrue_daily(self, session: AsyncSession) -> AccrueDaily:
        """Дейлик."""
        return AccrueDaily(self.uow_factory(session), self.infra.clock)

    def accrue_match(self, session: AsyncSession) -> AccrueMatch:
        """Онлайн-матч."""
        return AccrueMatch(self.uow_factory(session), self.infra.clock, self.reward_hook)

    def accrue_weekly_top(self, session: AsyncSession) -> AccrueWeeklyTop:
        """Топ недели."""
        return AccrueWeeklyTop(self.uow_factory(session), self.infra.clock)

    def accrue_season(self, session: AsyncSession) -> AccrueSeason:
        """Итог сезона."""
        return AccrueSeason(self.uow_factory(session), self.infra.clock)

    def tracker(self, session: AsyncSession) -> ProgressTracker:
        """Метрики, задания и бейджи в транзакции consumer."""
        return ProgressTracker(self.uow_factory(session), self.infra.clock)

    # ------------------------------------------------------------------ чтение
    @property
    def quest_queries(self) -> QuestQueries:
        """Задания игрока."""
        return QuestQueries(self.uow_factory(), self.infra.clock)

    @property
    def badge_queries(self) -> BadgeQueries:
        """Бейджи игрока."""
        return BadgeQueries(self.uow_factory())

    @property
    def wheel(self) -> WheelService:
        """Колесо: просмотр и спин."""
        # Источник читается при каждом спине: тесты подменяют ``random`` после сборки роутеров.
        return WheelService(self.uow_factory(), self.infra.clock, lambda: self.random)

    @cached_property
    def queries(self) -> ProgressQueries:
        """Чтение прогресса и кошелька."""
        return ProgressQueries(
            self.uow_factory(),
            self.infra.clock,
            recent_limit=self.settings.progression.wallet_recent_limit,
        )

    # ------------------------------------------------------------------ админка (поток P3)
    def admin_uow_factory(self) -> AdminUowFactory:
        """Фабрика UoW админки (своя транзакция)."""
        infra = self.infra
        producer = self.settings.service_name

        def factory() -> SqlAdminUnitOfWork:
            if infra.sessions is None:
                raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
            return SqlAdminUnitOfWork(producer=producer, clock=infra.clock, sessions=infra.sessions)

        return factory

    @cached_property
    def admin(self) -> AdminServices:
        """Use-cases admin-API."""
        factory = self.admin_uow_factory()
        clock = self.infra.clock
        items = ItemCommands(
            factory,
            clock,
            store=self.media_store,
            inspector=PillowMediaInspector(),
            tokens=SecretsTokenSource(),
        )
        events = EventAdminCommands(
            factory,
            clock,
            store=self.media_store,
            inspector=PillowMediaInspector(),
            tokens=SecretsTokenSource(),
        )
        return AdminServices(
            events=events,
            event_queries=EventAdminQueries(factory, clock, events),
            configs=ConfigCommands(factory, clock),
            config_queries=ConfigQueries(factory),
            ledger=LedgerCommands(factory, clock),
            ledger_queries=LedgerQueries(factory),
            items=items,
            item_queries=ItemQueries(factory, items),
            packs=PackCommands(factory, clock),
            pack_queries=PackQueries(factory),
            promos=PromoCommands(factory, clock, hasher=self.hasher, rng=self.code_random),
            promo_queries=PromoQueries(factory, clock),
        )

    # ------------------------------------------------------------------ магазин (поток S1)
    @cached_property
    def media_store(self) -> S3MediaStore:
        """Хранилище картинок предметов (подключение - при первом обращении)."""
        p = self.settings.progression
        return S3MediaStore(
            self.settings.s3, bucket=p.cosmetics_bucket, base_url=p.cosmetics_base_url
        )

    @cached_property
    def hasher(self) -> LazyCodeHasher:
        """HMAC кодов промокодов (ключ - при первом использовании)."""
        return LazyCodeHasher(lambda: hmac_key(self.settings))

    @cached_property
    def limiter(self) -> AttemptLimiter:
        """Лимитер попыток ввода кода: Redis, без Redis - ввод закрыт (fail-closed)."""
        retry = self.settings.progression.limiter_retry_after_s
        if self.infra.redis is None:
            return UnconfiguredLimiter(retry)
        return RedisAttemptLimiter(self.infra.redis, retry_after_s=retry)

    # ------------------------------------------------------------------ сезонные события (5d)
    @property
    def event_player(self) -> EventPlayerService:
        """События для игрока: страница, архив, claim (лимит на игрока - Redis, fail-closed)."""
        count, window = self.settings.progression.event_claim_rate
        return EventPlayerService(
            self.uow_factory(),
            self.infra.clock,
            self.media_store.public_url,
            limiter=_LateLimiter(self),
            claim_limit=count,
            claim_window_s=window,
        )

    @property
    def event_ticker(self) -> EventTicker:
        """Задача ``progression.events.tick``."""
        return EventTicker(self.uow_factory(), self.infra.clock)

    @property
    def shop_services(self) -> ShopServices:
        """Use-cases магазина для публичного API."""
        return ShopServices(
            shop=self.shop,
            inventory=self.inventory,
            appearance=self.appearance,
            promo=self.promo,
            public_url=self.media_store.public_url,
            avatars=self.avatar,
            ugc_url=self.ugc_url,
        )

    # ------------------------------------------------------------------ своя аватарка (поток S3)
    def ugc_url(self, public_key: str) -> str:
        """Публичный адрес одобренной аватарки: ``<ugc_base_url><u/hex.webp>``."""
        return f"{self.settings.progression.ugc_base_url}{public_key}"

    @cached_property
    def service_tokens(self) -> ServiceTokenProvider:
        """Сервисный токен для media-pipeline (создаётся при первом вызове; нет учётных данных -
        ``ServiceAuthNotConfiguredError``, клиент превращает его в 503)."""
        return build_service_token_provider(self.infra, self.settings.progression.media_audience)

    @cached_property
    def media_ugc(self) -> MediaUgcClient:
        """Клиент media-pipeline (тесты подставляют фейк присваиванием)."""
        p = self.settings.progression
        client = HttpMediaUgcClient(
            base_url=p.media_url,
            timeout_s=p.media_timeout_s,
            tokens=lambda: self.service_tokens,
            max_bytes=p.avatar_max_bytes,
        )
        self.infra.add_closer(client.aclose)
        return client

    @property
    def avatar(self) -> AvatarService:
        """Своя аватарка (чтение, заявка)."""
        p = self.settings.progression
        return AvatarService(
            self.uow_factory(),
            self.infra.clock,
            _LateMedia(self),
            limiter=_LateLimiter(self),
            submit_limit=p.avatar_submit_limit,
            submit_window_s=p.avatar_submit_window_s,
        )

    def avatar_moderation(self, session: AsyncSession) -> AvatarService:
        """Применение ``AssetModerated`` в транзакции consumer (media-клиент не нужен)."""
        return AvatarService(self.uow_factory(session), self.infra.clock, _LateMedia(self))

    @property
    def shop(self) -> ShopService:
        """Витрина и покупка."""
        return ShopService(
            self.uow_factory(),
            self.infra.clock,
            billing_enabled=self.settings.progression.billing_enabled,
        )

    @property
    def inventory(self) -> InventoryService:
        """Инвентарь."""
        return InventoryService(self.uow_factory(), self.infra.clock)

    @property
    def appearance(self) -> AppearanceService:
        """Оформление."""
        return AppearanceService(self.uow_factory(), self.infra.clock)

    @property
    def promo(self) -> PromoService:
        """Погашение промокодов."""
        return PromoService(self.uow_factory(), self.infra.clock, self.hasher, self.limiter)


_ATTR = "_progression_container"


def container_for(infra: Infrastructure) -> ProgressionContainer:
    """Один контейнер на процесс."""
    existing = getattr(infra, _ATTR, None)
    if isinstance(existing, ProgressionContainer):
        return existing
    container = ProgressionContainer(infra)
    setattr(infra, _ATTR, container)
    return container
