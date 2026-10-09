"""Внешние клиенты: OAuth Яндекса и отправка писем."""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage
from urllib.parse import urlencode

import aiosmtplib
import httpx
import structlog

from gv_core.errors import DependencyUnavailableError
from identity_service.application.ports import EmailMessage, ExternalProfile
from identity_service.application.security import OAuthStateError
from identity_service.domain import Provider

log = structlog.get_logger(__name__)

_SUBJECTS = {
    "verify": "Подтвердите e-mail в Guessverse",
    "reset": "Сброс пароля в Guessverse",
    "already_registered": "У вас уже есть аккаунт Guessverse",
}
_BODIES = {
    "verify": "Чтобы подтвердить адрес, откройте ссылку:\n{link}\n",
    "reset": "Чтобы задать новый пароль, откройте ссылку:\n{link}\n"
    "Если вы не запрашивали сброс, просто проигнорируйте письмо.\n",
    "already_registered": "Кто-то пытался зарегистрироваться с вашим адресом. Если это вы — "
    "войдите или восстановите пароль.\n",
}


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
    """Письма в лог (локальная разработка, демонстрация этапа 1).

    В production ссылка с секретом в лог не пишется — только факт и назначение письма.
    Адрес получателя не логируется никогда (ПДн).
    """

    def __init__(self, *, include_links: bool) -> None:
        self._include_links = include_links

    async def send(self, message: EmailMessage) -> None:
        """«Отправить» письмо."""
        if self._include_links and message.link:
            log.info("email.logged", purpose=message.purpose, link=message.link)
        else:
            log.info("email.logged", purpose=message.purpose)


class SmtpEmailSender:
    """Отправка через SMTP (STARTTLS по умолчанию)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        username: str | None = None,
        password: str | None = None,
        start_tls: bool = True,
        timeout_s: float = 10.0,
    ) -> None:
        self._host, self._port, self._from = host, port, sender
        self._user, self._password = username, password
        self._tls, self._timeout = start_tls, timeout_s

    async def send(self, message: EmailMessage) -> None:
        """Отправить письмо."""
        mime = MimeMessage()
        mime["From"] = self._from
        mime["To"] = message.to.value
        mime["Subject"] = _SUBJECTS[message.purpose]
        mime.set_content(_BODIES[message.purpose].format(link=message.link or ""))
        await aiosmtplib.send(
            mime,
            hostname=self._host,
            port=self._port,
            username=self._user,
            password=self._password,
            start_tls=self._tls,
            timeout=self._timeout,
        )
