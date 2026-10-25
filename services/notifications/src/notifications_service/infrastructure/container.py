"""Сборка use-cases notifications (composition): общая для API, consumer и воркера.

Адаптеры создаются без подключений: экспорт OpenAPI и тесты собирают сервис без
инфраструктуры; недоступная зависимость проявляется ошибкой при первом обращении.
Проверки «ошибка старта» (база ссылок, e-mail-бэкенд вне local/ci) выполняются только в
настоящем процессе - когда у ``Infrastructure`` есть база данных.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7
from gv_email import LogEmailSender
from gv_messaging.ports import TaskQueue
from gv_service import (
    Environment,
    Infrastructure,
    ServiceAuthNotConfiguredError,
    build_service_token_provider,
)
from notifications_service.application.audience import (
    EraseUser,
    FanOutAudience,
    PurgeErasedUsers,
    RecordRunFinished,
)
from notifications_service.application.broadcasts import (
    BroadcastQueries,
    CreateBroadcast,
    SendBroadcast,
)
from notifications_service.application.ingest import IngestEvent
from notifications_service.application.player import (
    DeviceService,
    NotificationQueries,
    PreferenceService,
)
from notifications_service.application.ports import (
    EmailDirectory,
    EmailSender,
    PushSender,
    RealtimePublisher,
    UnitOfWorkFactory,
    UserDirectory,
)
from notifications_service.application.type_flags import NotificationTypeAdmin
from notifications_service.application.type_switch import TypeSwitch
from notifications_service.application.unsubscribe import Unsubscribe
from notifications_service.domain.catalog import TypeCatalog
from notifications_service.infrastructure.config import load_catalog
from notifications_service.infrastructure.db.repositories import SqlUnitOfWork
from notifications_service.infrastructure.email import (
    GvEmailSender,
    HttpEmailDirectory,
    HttpUserDirectory,
    NullPushSender,
    UnconfiguredEmailDirectory,
    UnconfiguredUserDirectory,
    smtp_transport,
)
from notifications_service.infrastructure.limiter import RedisAttemptLimiter
from notifications_service.infrastructure.realtime import (
    CentrifugoPublisher,
    NullPublisher,
    PostCommitDispatcher,
)
from notifications_service.infrastructure.tasks import LazyRabbitTaskQueue
from notifications_service.settings import Settings

log = structlog.get_logger(__name__)

IDENTITY_AUDIENCE = "identity"
_LOCAL_ENVIRONMENTS = {Environment.LOCAL, Environment.CI}
_LOCAL_BASE_URL = "http://localhost:3000"


class StartupConfigError(Exception):
    """Конфигурация процесса неполна для этого окружения: сервис не стартует."""


@dataclass(frozen=True, slots=True)
class Adapters:
    """Подменяемые адаптеры (тесты подставляют фейки)."""

    realtime: RealtimePublisher | None = None
    email: EmailSender | None = None
    directory: EmailDirectory | None = None
    push: PushSender | None = None
    tasks: TaskQueue | None = None
    users: UserDirectory | None = None
    sleep: Callable[[float], Awaitable[None]] | None = None


class NotificationsContainer:
    """Use-cases сервиса поверх инфраструктуры процесса."""

    def __init__(
        self,
        infra: Infrastructure,
        adapters: Adapters | None = None,
        new_id: Callable[[], UUID] = uuid7,
    ) -> None:
        if not isinstance(infra.settings, Settings):
            raise TypeError("ожидаются настройки notifications")
        self.infra = infra
        self.settings: Settings = infra.settings
        self._cfg = self.settings.notifications
        self.new_id = new_id
        adapters = adapters or Adapters()
        self.catalog: TypeCatalog = load_catalog(self._cfg.types_path)
        self.type_switch = TypeSwitch(infra.clock, ttl_s=self._cfg.type_flags_cache_ttl_s)
        self.public_base_url = self._build_base_url()
        self.realtime: RealtimePublisher = adapters.realtime or self._build_realtime()
        self.dispatcher = PostCommitDispatcher(self.realtime)
        self.email: EmailSender = adapters.email or self._build_email()
        self.directory: EmailDirectory = adapters.directory or self._build_directory()
        self.users: UserDirectory = adapters.users or self._build_user_directory()
        self.sleep: Callable[[float], Awaitable[None]] = adapters.sleep or asyncio.sleep
        self.push: PushSender = adapters.push or NullPushSender()
        self.tasks: TaskQueue = adapters.tasks or self._build_tasks()

    # ------------------------------------------------------------------ сборка адаптеров
    @property
    def is_runtime(self) -> bool:
        """Настоящий процесс (есть база), а не экспорт OpenAPI."""
        return self.infra.sessions is not None

    @property
    def is_local(self) -> bool:
        """local/ci: допустимы временные секреты и письма в лог."""
        return self.settings.environment in _LOCAL_ENVIRONMENTS

    def _build_base_url(self) -> str:
        if self._cfg.public_base_url is not None:
            return self._cfg.public_base_url
        if self.is_local or not self.is_runtime:
            return _LOCAL_BASE_URL
        raise StartupConfigError("не задан GV_NOTIFICATIONS__PUBLIC_BASE_URL")

    def _build_realtime(self) -> RealtimePublisher:
        realtime = self.settings.realtime
        if realtime is None or realtime.api_url is None:
            return NullPublisher()
        publisher = CentrifugoPublisher(realtime)
        self.infra.add_closer(publisher.aclose)
        return publisher

    def _build_email(self) -> EmailSender:
        if self._cfg.email_backend == "smtp":
            return GvEmailSender(smtp_transport(self._cfg.smtp))
        if self.is_runtime and not self.is_local:
            raise StartupConfigError(
                "GV_NOTIFICATIONS__EMAIL_BACKEND=log допустим только в local/ci"
            )
        return GvEmailSender(LogEmailSender(include_secrets=self.is_local, log_body=True))

    def _build_directory(self) -> EmailDirectory:
        """Адреса из identity; без ``GV_SERVICE_AUTH__*`` - заглушка (рассылка ``failed``)."""
        try:
            tokens = build_service_token_provider(self.infra, IDENTITY_AUDIENCE)
        except ServiceAuthNotConfiguredError:
            log.warning("notifications.service_auth_not_configured", audience=IDENTITY_AUDIENCE)
            return UnconfiguredEmailDirectory()
        directory = HttpEmailDirectory(
            self._cfg.identity_url, tokens, timeout_s=self._cfg.identity_timeout_s
        )
        self.infra.add_closer(directory.aclose)
        return directory

    def _build_user_directory(self) -> UserDirectory:
        """Активные пользователи из identity; без ``GV_SERVICE_AUTH__*`` - заглушка."""
        try:
            tokens = build_service_token_provider(self.infra, IDENTITY_AUDIENCE)
        except ServiceAuthNotConfiguredError:
            return UnconfiguredUserDirectory()
        directory = HttpUserDirectory(
            self._cfg.identity_url, tokens, timeout_s=self._cfg.identity_timeout_s
        )
        self.infra.add_closer(directory.aclose)
        return directory

    def _build_tasks(self) -> TaskQueue:
        rabbit = self.settings.rabbitmq_url
        queue = LazyRabbitTaskQueue(
            rabbit.get_secret_value() if rabbit else None, self.settings.service_name
        )
        self.infra.add_closer(queue.close)
        return queue

    # ------------------------------------------------------------------ use-cases
    def uow_factory(self, session: AsyncSession | None = None) -> UnitOfWorkFactory:
        """Фабрика UoW: своя транзакция или SAVEPOINT в транзакции consumer."""
        infra = self.infra

        def factory() -> SqlUnitOfWork:
            if session is not None:
                return SqlUnitOfWork(infra.clock, session=session)
            if infra.sessions is None:
                raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
            return SqlUnitOfWork(infra.clock, sessions=infra.sessions)

        return factory

    def ingest(self, session: AsyncSession | None = None) -> IngestEvent:
        """Приём события (в транзакции consumer - с ``session``)."""
        return IngestEvent(
            self.uow_factory(session), self.catalog, self.infra.clock, self.new_id, self.type_switch
        )

    @property
    def queries(self) -> NotificationQueries:
        """Чтение уведомлений и отметки."""
        return NotificationQueries(self.uow_factory(), self.infra.clock)

    @property
    def preferences(self) -> PreferenceService:
        """Настройки и согласие."""
        return PreferenceService(self.uow_factory(), self.catalog, self.infra.clock)

    @property
    def devices(self) -> DeviceService:
        """Устройства."""
        return DeviceService(self.uow_factory(), self.infra.clock, self.new_id)

    @property
    def unsubscribe(self) -> Unsubscribe:
        """Отписка по ссылке."""
        cfg = self._cfg
        limiter = RedisAttemptLimiter(
            lambda: self.infra.redis,
            window_s=cfg.unsubscribe_window_s,
            retry_after_s=cfg.limiter_retry_after_s,
        )
        return Unsubscribe(
            limiter,
            self.uow_factory(),
            self.infra.clock,
            max_attempts=cfg.unsubscribe_attempts,
            window_s=cfg.unsubscribe_window_s,
        )

    def run_finished(self, session: AsyncSession | None = None) -> RecordRunFinished:
        """Запись завершения запуска (в транзакции consumer - с ``session``)."""
        return RecordRunFinished(self.uow_factory(session))

    def erase_user(self, session: AsyncSession | None = None) -> EraseUser:
        """Удаление данных игрока (в транзакции consumer - с ``session``)."""
        return EraseUser(self.uow_factory(session), self.infra.clock)

    @property
    def purge_erased(self) -> PurgeErasedUsers:
        """Очистка tombstone удалённых игроков по сроку."""
        return PurgeErasedUsers(
            self.uow_factory(),
            self.infra.clock,
            timedelta(days=self._cfg.erased_retention_days),
        )

    @property
    def fan_out(self) -> FanOutAudience:
        """Массовая доставка in-app по аудитории типа."""
        return FanOutAudience(
            self.uow_factory(),
            self.catalog,
            self.infra.clock,
            self.new_id,
            self.dispatcher,
            switch=self.type_switch,
            batch_size=self._cfg.fanout_batch_size,
            users=self.users,
            pause_s=self._cfg.fanout_batch_pause_s,
            page_retries=self._cfg.fanout_page_retries,
            retry_pause_s=self._cfg.fanout_retry_pause_s,
            sleep=self.sleep,
        )

    @property
    def create_broadcast(self) -> CreateBroadcast:
        """Постановка рассылки."""
        return CreateBroadcast(self.uow_factory(), self.tasks, self.infra.clock, self.new_id)

    @property
    def type_admin(self) -> NotificationTypeAdmin:
        """Admin-выключатели типов."""
        return NotificationTypeAdmin(
            self.uow_factory(), self.catalog, self.type_switch, self.infra.clock, self.new_id
        )

    @property
    def broadcast_queries(self) -> BroadcastQueries:
        """Список рассылок."""
        return BroadcastQueries(
            self.uow_factory(), audience_timeout_ms=self._cfg.audience_timeout_ms
        )

    @property
    def send_broadcast(self) -> SendBroadcast:
        """Воркер рассылки."""
        return SendBroadcast(
            self.uow_factory(),
            self.directory,
            self.email,
            self.infra.clock,
            public_base_url=self.public_base_url,
            footer=self._cfg.unsubscribe_footer,
            batch_size=self._cfg.broadcast_batch_size,
            token_ttl_days=self._cfg.unsubscribe_ttl_days,
        )


_ATTR = "_notifications_container"


def container_for(
    infra: Infrastructure, adapters: Adapters | None = None
) -> NotificationsContainer:
    """Один контейнер на процесс (``adapters`` учитываются при первом создании)."""
    existing = getattr(infra, _ATTR, None)
    if isinstance(existing, NotificationsContainer):
        return existing
    container = NotificationsContainer(infra, adapters)
    setattr(infra, _ATTR, container)
    return container
