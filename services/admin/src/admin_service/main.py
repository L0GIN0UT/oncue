"""Точка входа admin (Django 5.2): ``admin api|consumer|migrate|all`` (D7 плана этапа 1).

- ``api`` — ASGI-приложение Django под uvicorn;
- ``consumer`` — синхронизация зеркала пользователей по ``identity.user.v1``;
- ``migrate`` — ``manage.py migrate`` (отдельный job перед выкатом);
- ``all`` — api + consumer (если задан ``GV_KAFKA_BOOTSTRAP``) в одном процессе.

Каждый процесс, кроме ``migrate``, поднимает сервер метрик на ``GV_METRICS_PORT``.
SIGTERM/SIGINT: uvicorn дообрабатывает запросы, consumer завершает текущее сообщение.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Awaitable, Callable, Iterator
from enum import StrEnum
from typing import TYPE_CHECKING, cast

import structlog
import uvicorn

from admin_service.settings import Settings
from gv_observability.logging import configure_logging
from gv_service.metrics_server import MetricsServer

if TYPE_CHECKING:
    from gv_messaging.consumer import EventProcessor

log = structlog.get_logger(__name__)

DJANGO_SETTINGS = "admin_service.django_settings"


class Command(StrEnum):
    """Режимы запуска процесса admin."""

    API = "api"
    CONSUMER = "consumer"
    MIGRATE = "migrate"
    ALL = "all"


class _Server(uvicorn.Server):
    """uvicorn без собственных обработчиков сигналов: сигналами управляет Runtime."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class Runtime:
    """Процесс admin: набор компонентов и общий graceful shutdown."""

    def __init__(self, command: Command, settings: Settings) -> None:
        self.command = command
        self.settings = settings
        self.stop = asyncio.Event()
        self._servers: list[_Server] = []
        self._metrics: MetricsServer | None = None

    def request_shutdown(self) -> None:
        """Начать остановку (идемпотентно)."""
        if self.stop.is_set():
            return
        log.info("process.shutdown_requested")
        self.stop.set()
        for server in self._servers:
            server.should_exit = True
        if self._metrics is not None:
            self._metrics.stop()

    def components(self) -> dict[str, Callable[[], Awaitable[None]]]:
        """Компоненты для команды. Явно запрошенный, но не настроенный consumer — ошибка."""
        result: dict[str, Callable[[], Awaitable[None]]] = {}
        if self.command in {Command.API, Command.ALL}:
            result["api"] = self._run_api
        if self.command in {Command.CONSUMER, Command.ALL}:
            bootstrap = self.settings.kafka_bootstrap
            if bootstrap:
                result["consumer"] = lambda: self._run_consumer(bootstrap)
            elif self.command is Command.CONSUMER:
                raise SystemExit("consumer: задайте GV_KAFKA_BOOTSTRAP")
        if self.settings.metrics_port is not None:
            port = self.settings.metrics_port
            result["metrics"] = lambda: self._run_metrics(port)
        return result

    async def run(self) -> None:
        """Запустить компоненты и ждать их завершения; падение одного — остановка всех."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.request_shutdown)
        components = self.components()
        log.info("process.started", command=self.command, components=list(components))
        async with asyncio.TaskGroup() as group:
            for name, factory in components.items():
                group.create_task(self._guard(name, factory), name=name)
        log.info("process.stopped")

    async def _guard(self, name: str, factory: Callable[[], Awaitable[None]]) -> None:
        try:
            await factory()
        except Exception:
            log.exception("process.component_crashed", component=name)
            self.request_shutdown()
            raise

    async def _run_api(self) -> None:
        from admin_service.asgi import application

        s = self.settings
        config = uvicorn.Config(
            application,
            host=s.http_host,
            port=s.http_port,
            log_config=None,
            lifespan="off",  # Django не поддерживает ASGI lifespan
            proxy_headers=True,
            # X-Forwarded-For — только от доверенных прокси: от него зависит IP-allowlist.
            forwarded_allow_ips=s.admin.forwarded_allow_ips,
            timeout_graceful_shutdown=int(s.shutdown_timeout_s),
            server_header=False,
        )
        server = _Server(config)
        self._servers.append(server)
        if self.stop.is_set():
            return
        await server.serve()

    async def _run_consumer(self, bootstrap: str) -> None:
        from admin_service.core.events import GROUP, TOPICS, UserEventsProcessor
        from gv_messaging.kafka import (
            KafkaDeadLetterSink,
            KafkaEventConsumer,
            KafkaEventPublisher,
        )

        publisher = KafkaEventPublisher(bootstrap, client_id="admin-dlq")
        await publisher.start()
        try:
            processor = UserEventsProcessor(dead_letters=KafkaDeadLetterSink(publisher, GROUP))
            consumer = KafkaEventConsumer(
                bootstrap_servers=bootstrap,
                group=GROUP,
                topics=list(TOPICS),
                # KafkaEventConsumer вызывает только process(raw, source=...): обработчик
                # admin совместим по протоколу, но транзакция и inbox у него в Django ORM.
                processor=cast("EventProcessor", processor),
            )
            await consumer.run(self.stop)
        finally:
            await publisher.stop()

    async def _run_metrics(self, port: int) -> None:
        self._metrics = MetricsServer(self.settings.http_host, port)
        if self.stop.is_set():
            return
        await self._metrics.serve()


def setup_django() -> None:
    """Инициализировать Django (настройки из ``GV_*``)."""
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", DJANGO_SETTINGS)
    django.setup()


def run(argv: list[str] | None = None) -> None:
    """Консольная точка входа ``admin``."""
    args = sys.argv[1:] if argv is None else argv
    try:
        command = Command(args[0] if args else Command.ALL)
    except ValueError:
        sys.exit(
            f"неизвестная команда {args[0]!r}; доступны: {', '.join(c.value for c in Command)}"
        )
    settings = Settings()
    configure_logging(
        service=settings.service_name,
        env=settings.environment,
        level=settings.log_level,
        json=settings.log_json,
    )
    setup_django()
    if command is Command.MIGRATE:
        from django.core.management import call_command

        call_command("migrate", interactive=False, verbosity=1)
        log.info("migrations.applied")
        return
    asyncio.run(Runtime(command, settings).run())
