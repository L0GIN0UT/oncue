"""Отправка писем: log (local/ci), SMTP, фон; справочник адресов; Push-заглушка.

Адаптеры скопированы по образцу identity (``identity_service.infrastructure.clients``):
межсервисные импорты запрещены, вынос в общую библиотеку - отдельный хвост.
Адрес получателя не логируется никогда (ПДн).
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage as MimeMessage
from typing import Protocol
from uuid import UUID

import aiosmtplib
import httpx
import structlog

from gv_core.errors import DependencyUnavailableError
from notifications_service.application.ports import (
    DIRECTORY_FORBIDDEN,
    DIRECTORY_UNAVAILABLE,
    DIRECTORY_UNCONFIGURED,
    EmailMessage,
    EmailSender,
    PermanentEmailError,
    Recipient,
)
from notifications_service.settings import SmtpSettings

log = structlog.get_logger(__name__)


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
    """Адресов получателей нет: сервисная аутентификация не настроена (нет токенов identity).

    Рассылка в этом случае переходит в ``failed`` без повторов, остальной сервис работает.
    """

    async def resolve(self, user_ids: list[UUID]) -> list[Recipient]:  # noqa: ARG002 — порт
        """Всегда отказ."""
        raise DependencyUnavailableError(
            "справочник адресов не настроен", code=DIRECTORY_UNCONFIGURED
        )


class TokenProvider(Protocol):
    """Сервисный токен (``gv_auth.ServiceTokenProvider``)."""

    async def token(self) -> str:
        """Действующий токен."""
        ...

    def invalidate(self) -> None:
        """Забыть токен (ответ 401)."""
        ...


class HttpEmailDirectory:
    """Адреса подтверждённых e-mail через internal API identity.

    ``POST /internal/v1/users/emails`` (scope ``users.emails.read``): пачками до
    ``batch_size`` идентификаторов; в ответе только игроки с подтверждённым адресом, остальные
    пропускаются. Сетевой сбой, 5xx и 429 - повторяемая ошибка (воркер повторит задачу);
    прочие 4xx - неповторяемая настройка (``DIRECTORY_FORBIDDEN``). После 401 токен
    сбрасывается и вызов повторяется один раз. Адреса не попадают ни в логи, ни в исключения,
    ни в метрики; тело ответа об ошибке не читается.
    """

    PATH = "/internal/v1/users/emails"

    def __init__(
        self,
        base_url: str,
        tokens: TokenProvider,
        *,
        timeout_s: float,
        batch_size: int = 500,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._tokens = tokens
        self._batch = batch_size
        self._http = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def aclose(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._http.aclose()

    async def resolve(self, user_ids: list[UUID]) -> list[Recipient]:
        """Адреса игроков; без подтверждённого адреса - пропускаются.

        Raises:
            DependencyUnavailableError: ``DIRECTORY_UNAVAILABLE`` (повторить) или
                ``DIRECTORY_FORBIDDEN`` (настройка, повтор бесполезен).
        """
        result: list[Recipient] = []
        for start in range(0, len(user_ids), self._batch):
            chunk = user_ids[start : start + self._batch]
            result.extend(await self._fetch(chunk))
        return result

    async def _post(self, chunk: list[UUID]) -> httpx.Response:
        token = await self._tokens.token()
        try:
            return await self._http.post(
                self.PATH,
                json={"user_ids": [str(u) for u in chunk]},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            log.warning("notifications.email_directory_network", error=type(exc).__name__)
            raise DependencyUnavailableError(
                "identity недоступен", code=DIRECTORY_UNAVAILABLE
            ) from None

    async def _fetch(self, chunk: list[UUID]) -> list[Recipient]:
        response = await self._post(chunk)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            self._tokens.invalidate()
            response = await self._post(chunk)
            if response.status_code == httpx.codes.UNAUTHORIZED:
                self._tokens.invalidate()
        status = response.status_code
        if status != httpx.codes.OK:
            log.warning("notifications.email_directory_rejected", status=status)
            retryable = status >= httpx.codes.INTERNAL_SERVER_ERROR or (
                status == httpx.codes.TOO_MANY_REQUESTS
            )
            if retryable:
                raise DependencyUnavailableError(
                    "identity временно недоступен", code=DIRECTORY_UNAVAILABLE
                )
            raise DependencyUnavailableError(
                "identity отклонил запрос адресов: проверьте клиента и scope users.emails.read",
                code=DIRECTORY_FORBIDDEN,
            )
        try:
            return [
                Recipient(user_id=UUID(str(item["user_id"])), email=str(item["email"]))
                for item in response.json()["items"]
            ]
        except (KeyError, TypeError, ValueError):
            # Ответ может содержать адреса: исключение и лог без деталей.
            log.warning("notifications.email_directory_unparsable")
            raise DependencyUnavailableError(
                "ответ identity не разобран", code=DIRECTORY_UNAVAILABLE
            ) from None


class NullPushSender:
    """Push не реализован (мобильная версия после релиза): ничего не отправляет."""

    async def send(self, user_id: UUID, title: str, body: str) -> None:
        """Ничего не делает."""
