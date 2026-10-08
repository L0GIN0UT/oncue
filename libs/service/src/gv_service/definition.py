"""Описание сервиса и его инфраструктурные ресурсы.

Сервис объявляет, ИЗ ЧЕГО он состоит (``ServiceDefinition``), а ``gv_service.runner``
решает, КАК это запустить: api, consumer, worker, relay или всё в одном процессе.
Так у всех 15 сервисов одинаковые процессы, health, метрики и graceful shutdown.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from gv_core.clock import Clock, SystemClock
from gv_db.engine import create_engine, create_session_factory
from gv_db.metadata import ServiceTables
from gv_db.uow import SqlAlchemyUnitOfWork
from gv_messaging.consumer import EventHandlerRegistry
from gv_messaging.outbox import OutboxWriter
from gv_messaging.ports import TaskHandler
from gv_service.health import ReadinessCheck, postgres_check, redis_check
from gv_service.settings import ServiceSettings


@dataclass
class Infrastructure:
    """Ресурсы процесса, общие для всех компонентов сервиса.

    Создаётся один раз на процесс (``open``) и закрывается при остановке (``close``).
    """

    settings: ServiceSettings
    tables: ServiceTables | None
    clock: Clock = field(default_factory=SystemClock)
    engine: AsyncEngine | None = None
    sessions: async_sessionmaker[AsyncSession] | None = None
    redis: Redis | None = None

    @classmethod
    async def open(
        cls, settings: ServiceSettings, tables: ServiceTables | None, clock: Clock | None = None
    ) -> Infrastructure:
        """Создать подключения по настройкам. Отсутствующий адрес — ресурс не создаётся."""
        infra = cls(settings=settings, tables=tables, clock=clock or SystemClock())
        if settings.database_url is not None:
            infra.engine = create_engine(
                settings.database_url.get_secret_value(),
                pool_size=settings.database_pool_size,
                application_name=settings.service_name,
                behind_pgbouncer=settings.database_behind_pgbouncer,
            )
            infra.sessions = create_session_factory(infra.engine)
        if settings.redis_url is not None:
            infra.redis = Redis.from_url(
                settings.redis_url.get_secret_value(),
                socket_timeout=2,
                socket_connect_timeout=2,
                health_check_interval=30,
            )
        return infra

    async def close(self) -> None:
        """Закрыть подключения."""
        if self.redis is not None:
            await self.redis.aclose()
        if self.engine is not None:
            await self.engine.dispose()

    def readiness_checks(self) -> list[ReadinessCheck]:
        """Проверки готовности для созданных ресурсов."""
        checks: list[ReadinessCheck] = []
        if self.engine is not None:
            checks.append(postgres_check(self.engine))
        if self.redis is not None:
            checks.append(redis_check(self.redis))
        return checks

    def unit_of_work(self) -> SqlAlchemyUnitOfWork:
        """Новый Unit of Work для одной операции."""
        if self.sessions is None or self.tables is None:
            raise RuntimeError("у сервиса нет базы данных: задайте GV_DATABASE_URL и tables")
        return SqlAlchemyUnitOfWork(
            self.sessions,
            OutboxWriter(self.tables.outbox),
            producer=self.settings.service_name,
            clock=self.clock,
        )


def _no_routers(_: Infrastructure) -> list[APIRouter]:
    return []


@dataclass(frozen=True)
class ServiceDefinition:
    """Состав сервиса.

    Attributes:
        name: Имя сервиса (совпадает с ``GV_SERVICE_NAME`` и именем в реестре сервисов).
        settings_cls: Класс настроек сервиса.
        description: Описание для OpenAPI.
        tables: Метаданные БД; ``None`` — у сервиса нет своей базы.
        migrations_path: Каталог миграций Alembic.
        build_routers: Фабрика роутеров API (получает инфраструктуру для DI).
        build_event_handlers: Фабрика обработчиков доменных событий.
        consumer_topics: Топики, которые читает consumer.
        build_task_handlers: Фабрика обработчиков фоновых задач ``task_type → handler``.
    """

    name: str
    settings_cls: type[ServiceSettings]
    description: str = ""
    tables: ServiceTables | None = None
    migrations_path: Path | None = None
    build_routers: Callable[[Infrastructure], list[APIRouter]] = _no_routers
    build_event_handlers: Callable[[Infrastructure], EventHandlerRegistry] | None = None
    consumer_topics: tuple[str, ...] = ()
    build_task_handlers: Callable[[Infrastructure], dict[str, TaskHandler]] | None = None
