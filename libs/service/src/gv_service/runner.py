"""Точка входа процессов сервиса.

Использование (консольный скрипт сервиса)::

    <service> api        # HTTP API
    <service> relay      # публикация outbox → Redpanda
    <service> consumer   # обработка доменных событий
    <service> worker     # фоновые задачи RabbitMQ
    <service> all        # всё в одном процессе (VPS, локально)
    <service> migrate    # миграции БД (отдельный job перед выкатом)

Каждый процесс (кроме ``migrate``) поднимает сервер метрик на ``GV_METRICS_PORT``
(по умолчанию 9100); на основном HTTP-порту ``/metrics`` нет.

Graceful shutdown: по SIGTERM/SIGINT readiness сразу отвечает 503, HTTP-сервер дообрабатывает
текущие запросы, фоновые циклы завершают текущее сообщение и выходят; весь процесс
укладывается в ``shutdown_timeout_s``.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from collections.abc import Awaitable, Callable, Iterator
from enum import StrEnum

import structlog
import uvicorn

from gv_db.migrations import run_migrations
from gv_messaging.consumer import EventProcessor
from gv_messaging.inbox import Inbox
from gv_messaging.kafka import KafkaDeadLetterSink, KafkaEventConsumer, KafkaEventPublisher
from gv_messaging.outbox import OutboxRelay, RelaySettings
from gv_messaging.rabbit import RabbitTaskQueue
from gv_observability.logging import configure_logging
from gv_observability.tracing import configure_tracing
from gv_service.app import create_app
from gv_service.definition import Infrastructure, ServiceDefinition
from gv_service.metrics_server import MetricsServer
from gv_service.settings import ServiceSettings

log = structlog.get_logger(__name__)


class Command(StrEnum):
    """Режимы запуска процесса."""

    API = "api"
    RELAY = "relay"
    CONSUMER = "consumer"
    WORKER = "worker"
    ALL = "all"
    MIGRATE = "migrate"


class _Server(uvicorn.Server):
    """uvicorn без собственных обработчиков сигналов: сигналами управляет runner."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield

    def install_signal_handlers(self) -> None:  # совместимость со старыми версиями uvicorn
        return


class Runtime:
    """Один процесс сервиса: инфраструктура + набор фоновых компонентов."""

    def __init__(
        self, definition: ServiceDefinition, command: Command, settings: ServiceSettings
    ) -> None:
        self.definition = definition
        self.command = command
        self.settings = settings
        self.stop = asyncio.Event()
        self.shutting_down = False
        self._server: _Server | None = None
        self._metrics: MetricsServer | None = None

    def request_shutdown(self) -> None:
        """Начать graceful shutdown (идемпотентно)."""
        if self.shutting_down:
            return
        self.shutting_down = True
        log.info("process.shutdown_requested")
        self.stop.set()
        if self._server is not None:
            self._server.should_exit = True
        if self._metrics is not None:
            self._metrics.stop()

    async def run(self) -> None:
        """Запустить компоненты для выбранной команды и ждать их завершения."""
        s = self.settings
        tracing = configure_tracing(
            service=s.service_name,
            env=s.environment,
            otlp_endpoint=s.otlp_endpoint,
            sample_ratio=s.trace_sample_ratio,
        )
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.request_shutdown)

        infra = await Infrastructure.open(s, self.definition.tables)
        components = self._components(infra, tracing=tracing)
        log.info("process.started", command=self.command, components=list(components))
        try:
            async with asyncio.TaskGroup() as group:
                for name, factory in components.items():
                    group.create_task(self._guard(name, factory), name=name)
        finally:
            await infra.close()
            log.info("process.stopped")

    async def _guard(self, name: str, factory: Callable[[], Awaitable[None]]) -> None:
        """Если компонент упал — останавливаем весь процесс: оркестратор перезапустит его.

        Полуживой процесс (API работает, consumer мёртв) хуже честного перезапуска.
        """
        try:
            await factory()
        except Exception:
            log.exception("process.component_crashed", component=name)
            self.request_shutdown()
            raise

    def _components(
        self, infra: Infrastructure, *, tracing: bool
    ) -> dict[str, Callable[[], Awaitable[None]]]:
        wanted = {
            Command.API: ("api",),
            Command.RELAY: ("relay",),
            Command.CONSUMER: ("consumer",),
            Command.WORKER: ("worker",),
            Command.ALL: ("api", "relay", "consumer", "worker"),
        }[self.command]
        available: dict[str, Callable[[], Awaitable[None]] | None] = {
            "api": lambda: self._run_api(infra, tracing=tracing),
            "relay": self._relay_factory(infra),
            "consumer": self._consumer_factory(infra),
            "worker": self._worker_factory(infra),
        }
        components: dict[str, Callable[[], Awaitable[None]]] = {}
        for name in wanted:
            factory = available[name]
            if factory is not None:
                components[name] = factory
            elif self.command is not Command.ALL:
                # Явно запрошенный, но не сконфигурированный компонент — ошибка конфигурации.
                raise SystemExit(
                    f"компонент {name!r} не сконфигурирован для {self.definition.name}"
                )
        metrics_port = self.settings.metrics_port
        if metrics_port is not None:
            # Метрики — у каждого процесса, включая consumer/worker/relay без HTTP API.
            components["metrics"] = lambda: self._run_metrics(metrics_port)
        return components

    async def _run_metrics(self, port: int) -> None:
        self._metrics = MetricsServer(self.settings.http_host, port)
        if self.shutting_down:  # сигнал пришёл раньше, чем сервер создан
            return
        await self._metrics.serve()

    async def _run_api(self, infra: Infrastructure, *, tracing: bool) -> None:
        app = create_app(
            self.definition,
            infra,
            is_shutting_down=lambda: self.shutting_down,
            tracing_enabled=tracing,
        )
        config = uvicorn.Config(
            app,
            host=self.settings.http_host,
            port=self.settings.http_port,
            log_config=None,  # логирование уже настроено structlog
            proxy_headers=True,
            forwarded_allow_ips="*",  # сервис доступен только из внутренней сети за gateway
            timeout_graceful_shutdown=int(self.settings.shutdown_timeout_s),
            server_header=False,
        )
        self._server = _Server(config)
        await self._server.serve()

    def _relay_factory(self, infra: Infrastructure) -> Callable[[], Awaitable[None]] | None:
        s, tables = self.settings, self.definition.tables
        if tables is None or infra.sessions is None or not s.kafka_bootstrap:
            return None
        sessions, bootstrap = infra.sessions, s.kafka_bootstrap

        async def run() -> None:
            publisher = KafkaEventPublisher(bootstrap, client_id=f"{s.service_name}-relay")
            await publisher.start()
            try:
                relay = OutboxRelay(
                    tables.outbox,
                    sessions,
                    publisher,
                    infra.clock,
                    RelaySettings(lock_name=s.service_name),
                )
                await relay.run_forever(self.stop)
            finally:
                await publisher.stop()

        return run

    def _consumer_factory(self, infra: Infrastructure) -> Callable[[], Awaitable[None]] | None:
        d, s = self.definition, self.settings
        if (
            d.build_event_handlers is None
            or not d.consumer_topics
            or d.tables is None
            or infra.sessions is None
            or not s.kafka_bootstrap
        ):
            return None
        registry = d.build_event_handlers(infra)
        sessions, bootstrap, tables = infra.sessions, s.kafka_bootstrap, d.tables
        group = f"{s.service_name}.consumer"

        async def run() -> None:
            publisher = KafkaEventPublisher(bootstrap, client_id=f"{s.service_name}-dlq")
            await publisher.start()
            try:
                processor = EventProcessor(
                    group=group,
                    registry=registry,
                    session_factory=sessions,
                    inbox=Inbox(tables.inbox),
                    dead_letters=KafkaDeadLetterSink(publisher, group),
                    clock=infra.clock,
                )
                consumer = KafkaEventConsumer(
                    bootstrap_servers=bootstrap,
                    group=group,
                    topics=list(d.consumer_topics),
                    processor=processor,
                )
                await consumer.run(self.stop)
            finally:
                await publisher.stop()

        return run

    def _worker_factory(self, infra: Infrastructure) -> Callable[[], Awaitable[None]] | None:
        d, s = self.definition, self.settings
        if d.build_task_handlers is None or s.rabbitmq_url is None:
            return None
        handlers = d.build_task_handlers(infra)
        if not handlers:
            return None
        url = s.rabbitmq_url.get_secret_value()

        async def run() -> None:
            queue = RabbitTaskQueue(url, s.service_name)
            await queue.start()
            try:
                async with asyncio.TaskGroup() as group:
                    for task_type, handler in handlers.items():
                        group.create_task(queue.consume(task_type, handler, self.stop))
            finally:
                await queue.stop()

        return run


def main(definition: ServiceDefinition, argv: list[str] | None = None) -> None:
    """Консольная точка входа сервиса."""
    args = sys.argv[1:] if argv is None else argv
    try:
        command = Command(args[0] if args else Command.ALL)
    except ValueError:
        sys.exit(
            f"неизвестная команда {args[0]!r}; доступны: {', '.join(c.value for c in Command)}"
        )

    settings = definition.settings_cls()
    configure_logging(
        service=settings.service_name,
        env=settings.environment,
        level=settings.log_level,
        json=settings.log_json,
    )

    if command is Command.MIGRATE:
        if definition.migrations_path is None or settings.database_url is None:
            sys.exit("миграции не настроены: нужны migrations_path и GV_DATABASE_URL")
        run_migrations(definition.migrations_path, settings.database_url.get_secret_value())
        log.info("migrations.applied")
        return

    asyncio.run(Runtime(definition, command, settings).run())
