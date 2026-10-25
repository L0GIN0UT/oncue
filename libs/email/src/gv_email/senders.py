"""Отправители писем: SMTP, лог (local/ci), фоновая обёртка.

Поведение перенесено из identity и notifications без изменений. Адрес получателя не логируется
никогда (ПДн).
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage as MimeMessage
from typing import Protocol

import aiosmtplib
import structlog

from gv_email.message import OutgoingEmail, PermanentEmailError

log = structlog.get_logger(__name__)


class EmailTransport(Protocol):
    """Отправка готового письма. Сбой - исключение (вызывающий решает, что делать)."""

    async def send(self, message: OutgoingEmail) -> None:
        """Отправить."""
        ...


class LogEmailSender:
    """Письма в лог (только local/ci).

    ``secret_log_fields`` письма пишутся лишь при ``include_secrets``; тело - только при
    ``include_secrets`` и ``log_body`` (e2e читает ссылку из тела).
    """

    def __init__(self, *, include_secrets: bool = False, log_body: bool = False) -> None:
        self._include_secrets = include_secrets
        self._log_body = log_body

    async def send(self, message: OutgoingEmail) -> None:
        """«Отправить» письмо."""
        extra: dict[str, object] = dict(message.log_fields)
        if self._include_secrets:
            extra.update(message.secret_log_fields)
            if self._log_body:
                extra["body"] = message.body
        log.info("email.logged", subject=message.subject, **extra)


class BackgroundEmailSender:
    """Отправка вне запроса: время ответа не зависит от SMTP. Сбой логируется, не пробрасывается.

    Задача не переживает остановку процесса - письмо тогда не дойдёт (повтор на стороне
    пользователя). При ``max_pending`` задачах в полёте отправка идёт синхронно: очередь не
    растёт без границ.
    """

    def __init__(self, inner: EmailTransport, *, max_pending: int = 100) -> None:
        self._inner, self._max_pending = inner, max_pending
        self._tasks: set[asyncio.Task[None]] = set()

    async def send(self, message: OutgoingEmail) -> None:
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

    async def _deliver(self, message: OutgoingEmail) -> None:
        try:
            await self._inner.send(message)
        except Exception as exc:
            log.warning("email.send_failed", error=type(exc).__name__, **message.log_fields)


class SmtpEmailSender:
    """Отправка через SMTP: STARTTLS (по умолчанию) или неявный TLS (``use_tls``, порт 465)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        username: str | None = None,
        password: str | None = None,
        start_tls: bool = True,
        use_tls: bool = False,
        timeout_s: float = 10.0,
    ) -> None:
        if use_tls and start_tls:
            raise ValueError("use_tls и start_tls взаимоисключающи")
        self._host, self._port, self._from = host, port, sender
        self._user, self._password = username, password
        self._start_tls, self._use_tls, self._timeout = start_tls, use_tls, timeout_s

    async def send(self, message: OutgoingEmail) -> None:
        """Отправить письмо.

        Raises:
            PermanentEmailError: получатель отвергнут сервером (повтор не поможет). Ошибки
                входа, соединения и прочие 5xx - обычные исключения aiosmtplib (повторяемые).
        """
        mime = MimeMessage()
        mime["From"] = self._from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        for name, value in message.headers.items():
            mime[name] = value
        mime.set_content(message.body)
        try:
            await aiosmtplib.send(
                mime,
                hostname=self._host,
                port=self._port,
                username=self._user,
                password=self._password,
                start_tls=self._start_tls,
                use_tls=self._use_tls,
                timeout=self._timeout,
            )
        except (aiosmtplib.SMTPRecipientsRefused, aiosmtplib.SMTPRecipientRefused) as exc:
            raise PermanentEmailError("получатель отвергнут SMTP-сервером") from exc
