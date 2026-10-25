"""Отправка писем: log (local/ci), SMTP, фон; справочник адресов; Push-заглушка.

Отправители писем (SMTP, лог, фон) - общая библиотека ``libs/email`` (ADR 0066); здесь тонкий
адаптер порта. Адрес получателя не логируется никогда (ПДн).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import httpx
import structlog

from gv_core.errors import DependencyUnavailableError
from gv_email import EmailTransport, OutgoingEmail, SmtpEmailSender
from notifications_service.application.ports import (
    DIRECTORY_FORBIDDEN,
    DIRECTORY_UNAVAILABLE,
    DIRECTORY_UNCONFIGURED,
    EmailMessage,
    Recipient,
    UserPage,
)
from notifications_service.settings import SmtpSettings

log = structlog.get_logger(__name__)


class GvEmailSender:
    """Адаптер порта ``EmailSender`` к транспорту ``gv_email`` (SMTP, лог из ``libs/email``)."""

    def __init__(self, transport: EmailTransport) -> None:
        self._transport = transport

    async def send(self, message: EmailMessage) -> None:
        """Передать письмо транспорту; ``PermanentEmailError`` пробрасывается как есть."""
        await self._transport.send(
            OutgoingEmail(
                to=message.to, subject=message.subject, body=message.body, headers=message.headers
            )
        )


def smtp_transport(settings: SmtpSettings) -> SmtpEmailSender:
    """SMTP-транспорт из настроек сервиса (``GV_NOTIFICATIONS__SMTP__*``)."""
    return SmtpEmailSender(
        host=settings.host,
        port=settings.port,
        sender=settings.sender,
        username=settings.username,
        password=settings.password.get_secret_value() if settings.password else None,
        start_tls=settings.start_tls,
        use_tls=settings.use_tls,
        timeout_s=settings.timeout_s,
    )


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


class UnconfiguredUserDirectory:
    """Списка пользователей нет: сервисная аутентификация не настроена (нет токенов identity)."""

    async def active_after(self, after: UUID | None, limit: int) -> UserPage:  # noqa: ARG002 — порт
        """Всегда окончательный отказ (настройка, повтор бесполезен)."""
        raise DependencyUnavailableError(
            "список пользователей не настроен", code=DIRECTORY_UNCONFIGURED
        )


class HttpUserDirectory:
    """Активные пользователи через internal API identity (ADR 0069).

    ``GET /internal/v1/users/active?after=&limit=`` (scope ``users.active.list``): только
    идентификаторы. Сбой сети, 5xx и 429 - повторяемая ошибка (``DIRECTORY_UNAVAILABLE``);
    прочие 4xx - окончательная (``DIRECTORY_FORBIDDEN``); после 401 токен сбрасывается и вызов
    повторяется один раз. Тело ответа об ошибке не читается.
    """

    PATH = "/internal/v1/users/active"

    def __init__(
        self,
        base_url: str,
        tokens: TokenProvider,
        *,
        timeout_s: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._tokens = tokens
        self._http = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def aclose(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._http.aclose()

    async def _get(self, after: UUID | None, limit: int) -> httpx.Response:
        token = await self._tokens.token()
        params: dict[str, str | int] = {"limit": limit}
        if after is not None:
            params["after"] = str(after)
        try:
            return await self._http.get(
                self.PATH, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            log.warning("notifications.user_directory_network", error=type(exc).__name__)
            raise DependencyUnavailableError(
                "identity недоступен", code=DIRECTORY_UNAVAILABLE
            ) from None

    async def active_after(self, after: UUID | None, limit: int) -> UserPage:
        """Страница активных пользователей после курсора ``after``.

        Raises:
            DependencyUnavailableError: ``DIRECTORY_UNAVAILABLE`` (повторить) или
                ``DIRECTORY_FORBIDDEN`` (настройка, повтор бесполезен).
        """
        response = await self._get(after, limit)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            self._tokens.invalidate()
            response = await self._get(after, limit)
            if response.status_code == httpx.codes.UNAUTHORIZED:
                self._tokens.invalidate()
        status = response.status_code
        if status != httpx.codes.OK:
            log.warning("notifications.user_directory_rejected", status=status)
            if status >= httpx.codes.INTERNAL_SERVER_ERROR or (
                status == httpx.codes.TOO_MANY_REQUESTS
            ):
                raise DependencyUnavailableError(
                    "identity временно недоступен", code=DIRECTORY_UNAVAILABLE
                )
            raise DependencyUnavailableError(
                "identity отклонил запрос списка: проверьте клиента и scope users.active.list",
                code=DIRECTORY_FORBIDDEN,
            )
        try:
            body = response.json()
            cursor = body["next_cursor"]
            return UserPage(
                ids=[UUID(str(i)) for i in body["items"]],
                next_cursor=UUID(str(cursor)) if cursor is not None else None,
            )
        except (KeyError, TypeError, ValueError):
            log.warning("notifications.user_directory_unparsable")
            raise DependencyUnavailableError(
                "ответ identity не разобран", code=DIRECTORY_UNAVAILABLE
            ) from None


class NullPushSender:
    """Push не реализован (мобильная версия после релиза): ничего не отправляет."""

    async def send(self, user_id: UUID, title: str, body: str) -> None:
        """Ничего не делает."""
