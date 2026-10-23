"""Сборка use-cases progression (composition): общая для API, consumer и relay."""

from __future__ import annotations

from functools import cached_property

from sqlalchemy.ext.asyncio import AsyncSession

from gv_core.errors import DependencyUnavailableError
from gv_core.random import Random, SystemRandom
from gv_service import Infrastructure
from progression_service.application.accrual.daily import AccrueDaily
from progression_service.application.accrual.match import AccrueMatch
from progression_service.application.accrual.rating_rewards import AccrueSeason, AccrueWeeklyTop
from progression_service.application.accrual.solo import AccrueSolo
from progression_service.application.admin import AdminServices
from progression_service.application.admin.configs import ConfigCommands, ConfigQueries
from progression_service.application.admin.ledger import LedgerCommands, LedgerQueries
from progression_service.application.admin.ports import AdminUowFactory
from progression_service.application.badges import BadgeQueries
from progression_service.application.boosts import BoostRewardHook
from progression_service.application.ports import RewardHook, UnitOfWorkFactory
from progression_service.application.queries import ProgressQueries
from progression_service.application.quests import QuestQueries
from progression_service.application.quests.tracker import ProgressTracker
from progression_service.application.wheel import WheelService
from progression_service.infrastructure.db.admin_repositories import SqlAdminUnitOfWork
from progression_service.infrastructure.db.repositories import SqlProgressionUnitOfWork
from progression_service.settings import Settings


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
        return AdminServices(
            configs=ConfigCommands(factory, self.infra.clock),
            config_queries=ConfigQueries(factory),
            ledger=LedgerCommands(factory, self.infra.clock),
            ledger_queries=LedgerQueries(factory),
        )


_ATTR = "_progression_container"


def container_for(infra: Infrastructure) -> ProgressionContainer:
    """Один контейнер на процесс."""
    existing = getattr(infra, _ATTR, None)
    if isinstance(existing, ProgressionContainer):
        return existing
    container = ProgressionContainer(infra)
    setattr(infra, _ATTR, container)
    return container
