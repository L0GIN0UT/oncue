"""Сборка use-cases notifications (composition): общая для API, consumer и воркера.

Адаптеры создаются без подключений: экспорт OpenAPI и тесты собирают сервис без
инфраструктуры; недоступная зависимость проявляется ошибкой при первом обращении.
Проверки «ошибка старта» (ключ отписки, база ссылок вне local/ci) выполняются только в
настоящем процессе - когда у ``Infrastructure`` есть база данных.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7
from gv_messaging.ports import TaskQueue
from gv_service import (
    Environment,
    Infrastructure,
    ServiceAuthNotConfiguredError,
    build_service_token_provider,
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
)
from notifications_service.application.unsubscribe import Unsubscribe
from notifications_service.domain.catalog import TypeCatalog
from notifications_service.domain.tokens import UnsubscribeTokens
from notifications_service.infrastructure.config import load_catalog
from notifications_service.infrastructure.db.repositories import SqlUnitOfWork
from notifications_service.infrastructure.email import (
    HttpEmailDirectory,
    LogEmailSender,
    NullPushSender,
    SmtpEmailSender,
    UnconfiguredEmailDirectory,
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
        self.tokens = self._build_tokens()
        self.public_base_url = self._build_base_url()
        self.realtime: RealtimePublisher = adapters.realtime or self._build_realtime()
        self.dispatcher = PostCommitDispatcher(self.realtime)
        self.email: EmailSender = adapters.email or self._build_email()
        self.directory: EmailDirectory = adapters.directory or self._build_directory()
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

    def _build_tokens(self) -> UnsubscribeTokens:
        cfg = self._cfg
        keys = {v: k.get_secret_value().encode() for v, k in cfg.unsubscribe_previous_keys.items()}
        if cfg.unsubscribe_hmac_key is not None:
            keys[cfg.unsubscribe_key_version] = cfg.unsubscribe_hmac_key.get_secret_value().encode()
        elif self.is_local or not self.is_runtime:
            # Временный ключ процесса: ссылки живут до перезапуска. Вне local/ci в рабочем
            # процессе это ошибка старта (экспорт OpenAPI ключ не использует).
            keys[cfg.unsubscribe_key_version] = secrets.token_bytes(32)
        else:
            raise StartupConfigError("не задан GV_NOTIFICATIONS__UNSUBSCRIBE_HMAC_KEY")
        return UnsubscribeTokens(active_version=cfg.unsubscribe_key_version, keys=keys)

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
            return SmtpEmailSender(self._cfg.smtp)
        if self.is_runtime and not self.is_local:
            raise StartupConfigError(
                "GV_NOTIFICATIONS__EMAIL_BACKEND=log допустим только в local/ci"
            )
        return LogEmailSender(include_body=self.is_local)

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
        return IngestEvent(self.uow_factory(session), self.catalog, self.infra.clock, self.new_id)

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
            self.tokens,
            limiter,
            self.uow_factory(),
            self.infra.clock,
            max_attempts=cfg.unsubscribe_attempts,
            window_s=cfg.unsubscribe_window_s,
        )

    @property
    def create_broadcast(self) -> CreateBroadcast:
        """Постановка рассылки."""
        return CreateBroadcast(self.uow_factory(), self.tasks, self.infra.clock, self.new_id)

    @property
    def broadcast_queries(self) -> BroadcastQueries:
        """Список рассылок."""
        return BroadcastQueries(self.uow_factory())

    @property
    def send_broadcast(self) -> SendBroadcast:
        """Воркер рассылки."""
        return SendBroadcast(
            self.uow_factory(),
            self.directory,
            self.email,
            self.tokens,
            self.infra.clock,
            public_base_url=self.public_base_url,
            footer=self._cfg.unsubscribe_footer,
            batch_size=self._cfg.broadcast_batch_size,
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
