"""Отправка писем: log (local/ci), SMTP, фон; справочник адресов; Push-заглушка.

Адаптеры скопированы по образцу identity (``identity_service.infrastructure.clients``):
межсервисные импорты запрещены, вынос в общую библиотеку - отдельный хвост.
Адрес получателя не логируется никогда (ПДн).
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage as MimeMessage
from uuid import UUID

import aiosmtplib
import structlog

from gv_core.errors import DependencyUnavailableError
from notifications_service.application.ports import (
    EmailMessage,
    EmailSender,
    PermanentEmailError,
    Recipient,
)
from notifications_service.settings import SmtpSettings

log = structlog.get_logger(__name__)

DIRECTORY_UNCONFIGURED = "notifications.email_directory_unconfigured"


class LogEmailSender:
    """Письма в лог (только local/ci); тело - лишь при ``include_body`` (e2e читает ссылку)."""

    def __init__(self, *, include_body: bool = False) -> None:
        self._include_body = include_body

    async def send(self, message: EmailMessage) -> None:
        """«Отправить» письмо."""
        extra = {"body": message.body} if self._include_body else {}
        log.info("email.logged", subject=message.subject, **extra)


class BackgroundEmailSender:
    """Отправка вне запроса: время ответа не зависит от SMTP. Сбой логируется, не пробрасывается.

    При ``max_pending`` задачах в полёте отправка идёт синхронно: очередь не растёт без границ.
    """

    def __init__(self, inner: EmailSender, *, max_pending: int = 100) -> None:
        self._inner, self._max_pending = inner, max_pending
        self._tasks: set[asyncio.Task[None]] = set()

    async def send(self, message: EmailMessage) -> None:
        """Поставить письмо в фон (или отправить сразу, если очередь заполнена)."""
        if len(self._tasks) >= self._max_pending:
            await self._deliver(message)
            return
        task = asyncio.get_running_loop().create_task(self._deliver(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self, timeout_s: float = 10.0) -> None:
        """Дождаться уже запущенных отправок (остановка процесса, тесты)."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout_s)

    async def _deliver(self, message: EmailMessage) -> None:
        try:
            await self._inner.send(message)
        except Exception as exc:
            log.warning("email.send_failed", error=type(exc).__name__)


class SmtpEmailSender:
    """Отправка через SMTP: STARTTLS (по умолчанию) или неявный TLS (порт 465)."""

    def __init__(self, settings: SmtpSettings) -> None:
        self._s = settings

    async def send(self, message: EmailMessage) -> None:
        """Отправить письмо."""
        mime = MimeMessage()
        mime["From"] = self._s.sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        for name, value in message.headers.items():
            mime[name] = value
        mime.set_content(message.body)
        try:
            await aiosmtplib.send(
                mime,
                hostname=self._s.host,
                port=self._s.port,
                username=self._s.username,
                password=self._s.password.get_secret_value() if self._s.password else None,
                start_tls=self._s.start_tls,
                use_tls=self._s.use_tls,
                timeout=self._s.timeout_s,
            )
        except (aiosmtplib.SMTPRecipientsRefused, aiosmtplib.SMTPRecipientRefused) as exc:
            # Получатель отвергнут сервером: повтор не поможет. Ошибки входа, соединения
            # и прочие 5xx (проблема нашей стороны) остаются обычными - их повторят.
            raise PermanentEmailError("получатель отвергнут SMTP-сервером") from exc


class UnconfiguredEmailDirectory:
    """Адресов получателей нет: у identity пока нет internal-ручки выдачи адресов (хвост 5b).

    Рассылка в этом случае переходит в ``failed`` без повторов, остальной сервис работает.
    """

    async def resolve(self, user_ids: list[UUID]) -> list[Recipient]:  # noqa: ARG002 — порт
        """Всегда отказ."""
        raise DependencyUnavailableError(
            "справочник адресов не настроен", code=DIRECTORY_UNCONFIGURED
        )


class NullPushSender:
    """Push не реализован (мобильная версия после релиза): ничего не отправляет."""

    async def send(self, user_id: UUID, title: str, body: str) -> None:
        """Ничего не делает."""
