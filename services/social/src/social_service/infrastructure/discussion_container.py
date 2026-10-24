"""Сборка use-cases комментариев, жалоб и проекций (composition): общая для API и consumer.

Адаптеры создаются без подключений: экспорт OpenAPI и тесты собирают сервис без
инфраструктуры; недоступная зависимость проявляется ошибкой при первом обращении.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7
from gv_service import Infrastructure
from gv_text.ugc import UgcPolicy
from social_service.application.comments import CommentService
from social_service.application.ports.config import ConfigProvider
from social_service.application.ports.discussion import DiscussionUowFactory, RateLimiter
from social_service.application.projections import ProjectionService
from social_service.application.reports import ReportService
from social_service.infrastructure.config_provider import config_provider_for
from social_service.infrastructure.db.repositories_moderation import SqlDiscussionUnitOfWork
from social_service.infrastructure.limiter import RedisRateLimiter


@dataclass(frozen=True, slots=True)
class Adapters:
    """Подменяемые адаптеры (тесты подставляют фейки)."""

    config: ConfigProvider | None = None
    limiter: RateLimiter | None = None
    ugc: UgcPolicy | None = None


class DiscussionContainer:
    """Use-cases комментариев и жалоб поверх инфраструктуры процесса."""

    def __init__(
        self,
        infra: Infrastructure,
        adapters: Adapters | None = None,
        new_id: Callable[[], UUID] = uuid7,
    ) -> None:
        adapters = adapters or Adapters()
        self.infra = infra
        self.new_id = new_id
        self.config: ConfigProvider = adapters.config or config_provider_for(infra)
        self.ugc = adapters.ugc or UgcPolicy.from_file()
        self.limiter: RateLimiter = adapters.limiter or RedisRateLimiter(lambda: infra.redis)

    def uow_factory(self, session: AsyncSession | None = None) -> DiscussionUowFactory:
        """Фабрика UoW: своя транзакция или SAVEPOINT в транзакции consumer."""
        infra = self.infra

        def factory() -> SqlDiscussionUnitOfWork:
            producer = infra.settings.service_name
            if session is not None:
                return SqlDiscussionUnitOfWork(
                    producer=producer, clock=infra.clock, session=session
                )
            if infra.sessions is None:
                raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
            return SqlDiscussionUnitOfWork(
                producer=producer, clock=infra.clock, sessions=infra.sessions
            )

        return factory

    @property
    def comments(self) -> CommentService:
        """Комментарии."""
        return CommentService(
            self.uow_factory(), self.config, self.ugc, self.limiter, self.infra.clock, self.new_id
        )

    def reports(self, session: AsyncSession | None = None) -> ReportService:
        """Жалобы (в транзакции consumer - с ``session``)."""
        return ReportService(self.uow_factory(session), self.config, self.infra.clock, self.new_id)

    def projections(self, session: AsyncSession) -> ProjectionService:
        """Применение событий в транзакции consumer."""
        return ProjectionService(self.uow_factory(session), self.reports(session), self.infra.clock)


_ATTR = "_discussion_container"


def container_for(infra: Infrastructure, adapters: Adapters | None = None) -> DiscussionContainer:
    """Один контейнер на процесс (``adapters`` учитываются при первом создании)."""
    existing = getattr(infra, _ATTR, None)
    if isinstance(existing, DiscussionContainer):
        return existing
    container = DiscussionContainer(infra, adapters)
    setattr(infra, _ATTR, container)
    return container
