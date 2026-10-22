"""Внешние клиенты: OAuth Яндекса и отправка писем."""

from __future__ import annotations

import asyncio
from email.message import EmailMessage as MimeMessage
from urllib.parse import urlencode

import aiosmtplib
import httpx
import structlog

from gv_core.errors import DependencyUnavailableError
from identity_service.application.ports import EmailMessage, EmailSender, ExternalProfile
from identity_service.application.security import OAuthStateError
from identity_service.domain import Provider

log = structlog.get_logger(__name__)

_SUBJECTS = {
    "code_register": "Код подтверждения e-mail в Guessverse",
    "code_login": "Код для входа в Guessverse",
    "code_reset": "Код для сброса пароля в Guessverse",
    "code_password_change": "Код для смены пароля в Guessverse",
    "already_registered": "У вас уже есть аккаунт Guessverse",
    "provider_linked": "К аккаунту Guessverse привязан внешний сервис",
    "provider_unlinked": "От аккаунта Guessverse отвязан внешний сервис",
    "password_changed": "Пароль в Guessverse изменён",
}
_CODE_TAIL = "Код действует {minutes} мин. Если это были не вы — просто проигнорируйте письмо.\n"
_BODIES = {
    "code_register": "Код подтверждения e-mail: {code}\n" + _CODE_TAIL,
    "code_login": "Код для входа с нового устройства: {code}\n" + _CODE_TAIL,
    "code_reset": "Код для сброса пароля: {code}\n" + _CODE_TAIL,
    "code_password_change": "Код для смены пароля: {code}\n" + _CODE_TAIL,
    "already_registered": "Кто-то пытался зарегистрироваться с вашим адресом. Если это вы — "
    "войдите или восстановите пароль.\n",
    "provider_linked": "К вашему аккаунту привязан сервис: {providers}. Через него можно "
    "входить в Guessverse. Если это были не вы — смените пароль и отвяжите сервис в профиле.\n",
    "provider_unlinked": "От вашего аккаунта отвязан сервис: {providers}. Если это были не "
    "вы — смените пароль.\n",
    "password_changed": "Пароль вашего аккаунта изменён, все сеансы завершены.\n"
    "Привязанные сервисы, через которые можно войти: {providers}. Если какой-то из них не ваш — "
    "отвяжите его в профиле.\n",
}
_SECONDS_IN_MINUTE = 60


def render_email(message: EmailMessage, *, code_ttl_s: int) -> tuple[str, str]:
    """Тема и текст письма.

    Args:
        message: Письмо; для ``code_*`` обязателен ``code``.
        code_ttl_s: Срок действия кода в секундах (``GV_EMAIL_CODE__TTL_S``), в тексте — минуты.

    Returns:
        ``(subject, body)``. Ссылок с секретами в письмах нет (ADR 0041).
    """
    body = _BODIES[message.purpose].format(
        code=message.code or "",
        minutes=max(1, -(-code_ttl_s // _SECONDS_IN_MINUTE)),
        providers=", ".join(message.providers) or "нет",
    )
    return _SUBJECTS[message.purpose], body


class YandexOAuthProvider:
    """Яндекс ID: authorization code + PKCE S256.

    Используем только идентификатор (``id``) и логин для отображения; e-mail от Яндекса не
    запрашиваем и не сохраняем — он не участвует в связывании аккаунтов (D2, §8 п.5).
    """

    provider = Provider.YANDEX

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        authorize_url: str = "https://oauth.yandex.ru/authorize",
        token_url: str = "https://oauth.yandex.ru/token",  # noqa: S107 — URL, не секрет
        info_url: str = "https://login.yandex.ru/info",
        timeout_s: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id, self._secret, self._redirect = client_id, client_secret, redirect_uri
        self._authorize, self._token, self._info = authorize_url, token_url, info_url
        self._timeout, self._transport = timeout_s, transport

    def authorize_url(self, *, state: str, code_challenge: str) -> str:
        """URL согласия; ``redirect_uri`` — строго из конфигурации."""
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": self._redirect,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self._authorize}?{query}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def exchange_code(self, *, code: str, code_verifier: str) -> str:
        """Обменять код на токен Яндекса (токен не сохраняется)."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": self._client_id,
            "client_secret": self._secret,
            "redirect_uri": self._redirect,
        }
        try:
            async with self._client() as client:
                response = await client.post(self._token, data=data)
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError("Яндекс ID недоступен") from exc
        if response.status_code == 400:  # noqa: PLR2004 — invalid_grant: код истёк или чужой
            raise OAuthStateError("код авторизации недействителен", code="auth.oauth_code_invalid")
        if response.status_code != 200:  # noqa: PLR2004
            log.warning("auth.oauth_token_failed", provider="yandex", status=response.status_code)
            raise DependencyUnavailableError("Яндекс ID недоступен")
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise DependencyUnavailableError("Яндекс ID вернул некорректный ответ")
        return token

    async def fetch_profile(self, access_token: str) -> ExternalProfile:
        """Идентификатор пользователя Яндекса."""
        try:
            async with self._client() as client:
                response = await client.get(
                    self._info,
                    params={"format": "json"},
                    headers={"Authorization": f"OAuth {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError("Яндекс ID недоступен") from exc
        if response.status_code != 200:  # noqa: PLR2004
            raise DependencyUnavailableError("Яндекс ID недоступен")
        data = response.json()
        subject = data.get("id")
        if not isinstance(subject, str | int) or not str(subject):
            raise DependencyUnavailableError("Яндекс ID вернул некорректный профиль")
        login = data.get("login")
        return ExternalProfile(
            subject=str(subject), display=str(login)[:255] if isinstance(login, str) else None
        )


class LogEmailSender:
    """Письма в лог (local/ci).

    Код пишется в лог только при ``include_secrets`` (local/ci, e2e читают код оттуда);
    иначе — лишь факт и назначение. Адрес получателя не логируется никогда (ПДн).
    """

    def __init__(self, *, include_secrets: bool = False) -> None:
        self._include_secrets = include_secrets

    async def send(self, message: EmailMessage) -> None:
        """«Отправить» письмо."""
        extra: dict[str, object] = {}
        if message.providers:
            extra["providers"] = list(message.providers)
        if self._include_secrets:
            if message.code:
                extra["code"] = message.code
            if message.link:
                extra["link"] = message.link
        if message.challenge_id is not None:
            extra["challenge_id"] = str(message.challenge_id)  # не секрет: по нему e2e ищет код
        log.info("email.logged", purpose=message.purpose, **extra)


class BackgroundEmailSender:
    """Отправка письма вне запроса: время ответа не зависит от SMTP (ADR 0041, ревью).

    Иначе ``forgot`` отвечал бы дольше для существующего адреса (письмо уходит только ему), и по
    времени ответа адрес был бы различим. Задача не переживает остановку процесса — письмо
    тогда не дойдёт, пользователь запросит код повторно (resend). При ``max_pending`` задачах в
    полёте отправка идёт синхронно: очередь не растёт без границ.
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
            log.warning("email.send_failed", purpose=message.purpose, error=type(exc).__name__)


class SmtpEmailSender:
    """Отправка через SMTP: STARTTLS (по умолчанию) или неявный TLS (``use_tls``, порт 465)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        code_ttl_s: int,
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
        self._code_ttl_s = code_ttl_s

    async def send(self, message: EmailMessage) -> None:
        """Отправить письмо."""
        subject, body = render_email(message, code_ttl_s=self._code_ttl_s)
        mime = MimeMessage()
        mime["From"] = self._from
        mime["To"] = message.to.value
        mime["Subject"] = subject
        mime.set_content(body)
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
