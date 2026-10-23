"""Сборка use-cases rating (composition): общая для API, consumer и relay."""

from __future__ import annotations

from datetime import timedelta
from functools import cached_property

from sqlalchemy.ext.asyncio import AsyncSession

from gv_core.errors import DependencyUnavailableError
from gv_service import Infrastructure
from rating_service.application.commands.ladders import LadderCommands, LadderQueries
from rating_service.application.commands.leaderboards import Leaderboards
from rating_service.application.commands.match_rating import RateMatch
from rating_service.application.commands.seasons import SeasonCommands, SeasonLifecycle
from rating_service.application.commands.users import UserDirectory
from rating_service.application.commands.weekly import FinalizeWeeklyTops
from rating_service.application.ports import LeaderboardStore, UnitOfWorkFactory
from rating_service.application.queries import RatingQueries
from rating_service.infrastructure.db.repositories import SqlRatingUnitOfWork
from rating_service.infrastructure.leaderboard import RedisLeaderboard
from rating_service.settings import Settings


class RatingContainer:
    """Use-cases сервиса поверх инфраструктуры процесса."""

    def __init__(self, infra: Infrastructure, store: LeaderboardStore | None = None) -> None:
        if not isinstance(infra.settings, Settings):
            raise TypeError("ожидаются настройки rating")
        self.infra = infra
        self.settings: Settings = infra.settings
        self.store: LeaderboardStore = store or RedisLeaderboard(
            infra.redis, ready_ttl_s=self.settings.rating.leaderboard_resync_s
        )

    def uow_factory(self, session: AsyncSession | None = None) -> UnitOfWorkFactory:
        """Фабрика UoW: своя транзакция или SAVEPOINT в транзакции consumer."""
        infra = self.infra
        producer = self.settings.service_name
        batch = self.settings.rating.batch_size

        def factory() -> SqlRatingUnitOfWork:
            if session is not None:
                return SqlRatingUnitOfWork(
                    producer=producer, clock=infra.clock, carry_over_batch=batch, session=session
                )
            if infra.sessions is None:
                raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
            return SqlRatingUnitOfWork(
                producer=producer,
                clock=infra.clock,
                carry_over_batch=batch,
                sessions=infra.sessions,
            )

        return factory

    # ------------------------------------------------------------------ use-cases
    @cached_property
    def boards(self) -> Leaderboards:
        """Лидерборды."""
        return Leaderboards(
            self.uow_factory(),
            self.store,
            self.infra.clock,
            claim_ttl_s=self.settings.rating.rebuild_claim_ttl_s,
        )

    @cached_property
    def lifecycle(self) -> SeasonLifecycle:
        """Жизненный цикл сезона."""
        s = self.settings.rating
        return SeasonLifecycle(
            self.infra.clock,
            standings_top_size=s.standings_top_size,
            standings_chunk_size=s.standings_chunk_size,
        )

    @cached_property
    def ladder_commands(self) -> LadderCommands:
        """Мутации ladder-ов."""
        return LadderCommands(self.uow_factory(), self.infra.clock, self.lifecycle, self.boards)

    @cached_property
    def ladder_queries(self) -> LadderQueries:
        """Чтение ladder-ов для админки."""
        return LadderQueries(self.uow_factory())

    @cached_property
    def season_commands(self) -> SeasonCommands:
        """Команды и расписание сезонов."""
        s = self.settings.rating
        return SeasonCommands(
            self.uow_factory(),
            self.infra.clock,
            self.lifecycle,
            self.boards,
            start_skew=timedelta(seconds=s.season_start_skew_s),
            start_max_ahead=timedelta(days=s.season_start_max_ahead_days),
        )

    @cached_property
    def weekly(self) -> FinalizeWeeklyTops:
        """Закрытие топа недели."""
        return FinalizeWeeklyTops(self.uow_factory(), self.infra.clock)

    @cached_property
    def queries(self) -> RatingQueries:
        """Публичное чтение."""
        return RatingQueries(
            self.uow_factory(),
            self.infra.clock,
            self.store,
            self.boards,
            max_offset=self.settings.rating.leaderboard_max_offset,
        )

    def rate_match(self, session: AsyncSession) -> RateMatch:
        """Расчёт матча внутри транзакции consumer."""
        return RateMatch(
            self.uow_factory(session),
            self.infra.clock,
            self.boards,
            max_future_skew=timedelta(seconds=self.settings.rating.finished_at_skew_s),
        )

    @cached_property
    def users(self) -> UserDirectory:
        """Проекция профилей."""
        return UserDirectory(self.boards)


_ATTR = "_rating_container"


def container_for(infra: Infrastructure, store: LeaderboardStore | None = None) -> RatingContainer:
    """Один контейнер на процесс; явный ``store`` (тесты) заменяет контейнер процесса."""
    existing = getattr(infra, _ATTR, None)
    if isinstance(existing, RatingContainer) and store in (None, existing.store):
        return existing
    container = RatingContainer(infra, store)
    setattr(infra, _ATTR, container)
    return container
