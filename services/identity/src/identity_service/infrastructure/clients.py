"""Внешние клиенты: OAuth Яндекса и отправка писем."""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
import structlog

from gv_core.errors import DependencyUnavailableError
from gv_email import EmailTransport, OutgoingEmail
from identity_service.application.ports import EmailMessage, ExternalProfile
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


class IdentityEmailSender:
    """Тонкий адаптер порта ``EmailSender``: рендер письма identity и передача транспорту.

    Транспорт (SMTP, лог, фон) - общая библиотека ``libs/email``. Секреты (код, ссылка) идут
    только в ``secret_log_fields`` - в лог они попадают в local/ci.
    """

    def __init__(self, transport: EmailTransport, *, code_ttl_s: int) -> None:
        self._transport = transport
        self._code_ttl_s = code_ttl_s

    async def send(self, message: EmailMessage) -> None:
        """Отрендерить и отправить письмо."""
        subject, body = render_email(message, code_ttl_s=self._code_ttl_s)
        log_fields: dict[str, object] = {"purpose": message.purpose}
        if message.providers:
            log_fields["providers"] = list(message.providers)
        if message.challenge_id is not None:
            log_fields["challenge_id"] = str(
                message.challenge_id
            )  # не секрет, по нему e2e ищет код
        secret_fields: dict[str, object] = {}
        if message.code:
            secret_fields["code"] = message.code
        if message.link:
            secret_fields["link"] = message.link
        await self._transport.send(
            OutgoingEmail(
                to=message.to.value,
                subject=subject,
                body=body,
                log_fields=log_fields,
                secret_log_fields=secret_fields,
            )
        )
