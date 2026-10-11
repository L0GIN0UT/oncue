"""Выдача сервисных токенов (client credentials, D13 плана этапа 2)."""

from __future__ import annotations

import hmac
from dataclasses import dataclass

import structlog

from gv_core.errors import ForbiddenError, UnauthenticatedError
from identity_service.application.ports import RateLimiter
from identity_service.application.ports.service_tokens import (
    IssuedServiceToken,
    ServiceTokenSigner,
)
from identity_service.application.security import (
    NO_IP,
    ensure_allowed,
    hash_secret,
    rate_key,
)
from identity_service.domain.service_client import ServiceClientRegistry

log = structlog.get_logger(__name__)

# Сравниваем с чем-то и для неизвестного клиента: время ответа не выдаёт, какие client_id
# существуют.
_DUMMY_SHA256 = "0" * 64


@dataclass(frozen=True, slots=True)
class ServiceTokenConfig:
    """Параметры выдачи.

    Attributes:
        ttl_s: Время жизни токена (``GV_SERVICE_TOKEN__TTL_S``).
        failures_per_client_ip_per_min: Неудачных попыток на пару (``client_id``, IP) в минуту
            (``GV_SERVICE_TOKEN_LIMIT__FAILURES_PER_CLIENT_IP_PER_MIN``).
        success_per_client_per_min: Выданных токенов на ``client_id`` в минуту
            (``GV_SERVICE_TOKEN_LIMIT__SUCCESS_PER_CLIENT_PER_MIN``).
    """

    ttl_s: int = 600
    failures_per_client_ip_per_min: int = 30
    success_per_client_per_min: int = 600


class IssueServiceToken:
    """Выдать сервисный JWT по ``client_id`` и секрету.

    Порядок: секрет → адресат → лимит успешных выдач. Неизвестный клиент и неверный секрет
    неразличимы (401 ``auth.client_invalid``, ``compare_digest`` выполняется и для
    неизвестного клиента); адресат проверяется только после успешной аутентификации, поэтому
    403 ``auth.audience_forbidden`` не раскрывает конфигурацию постороннему.

    Лимиты раздельные, чтобы чужие неверные секреты не отрезали game и admin от токенов:
    неудачи считаются по паре (``client_id``, IP источника) — шквал с одного адреса не мешает
    честной реплике с другого; успешные выдачи — по ``client_id`` с высоким порогом (защита
    от зациклившегося клиента). Лимитер fail-open (как вход паролем): перебор 256-битного
    секрета невозможен и без него.
    """

    def __init__(
        self,
        clients: ServiceClientRegistry,
        signer: ServiceTokenSigner,
        limiter: RateLimiter,
        config: ServiceTokenConfig,
    ) -> None:
        self._clients = clients
        self._signer = signer
        self._limiter = limiter
        self._config = config

    async def execute(
        self, client_id: str, client_secret: str, audience: str, *, source_ip: str | None = None
    ) -> IssuedServiceToken:
        """Проверить учётные данные и выпустить токен.

        Args:
            client_id: Клиент.
            client_secret: Секрет клиента.
            audience: Вызываемый сервис.
            source_ip: IP источника запроса (ключ лимита неудач).

        Raises:
            RateLimitedError: исчерпан лимит неудач с этого IP или успешных выдач клиента (429).
            UnauthenticatedError: ``auth.client_invalid`` — нет клиента или секрет неверен.
            ForbiddenError: ``auth.audience_forbidden`` — адресат клиенту не разрешён.
        """
        client = self._clients.get(client_id)
        expected = client.secret_sha256 if client is not None else _DUMMY_SHA256
        secret_ok = hmac.compare_digest(hash_secret(client_secret), expected)
        if client is None or not secret_ok:
            log.warning("auth.service_token_denied", client_id=client_id, reason="credentials")
            ensure_allowed(
                await self._limiter.hit(
                    rate_key("svc_token_fail", f"{client_id}|{source_ip or NO_IP}"),
                    limit=self._config.failures_per_client_ip_per_min,
                    window_s=60,
                )
            )
            raise UnauthenticatedError(
                "неверные учётные данные клиента", code="auth.client_invalid"
            )
        scopes = client.scopes_for(audience)
        if scopes is None:
            log.warning(
                "auth.service_token_denied",
                client_id=client_id,
                audience=audience,
                reason="audience",
            )
            raise ForbiddenError("адресат не разрешён клиенту", code="auth.audience_forbidden")
        ensure_allowed(
            await self._limiter.hit(
                rate_key("svc_token_ok", client_id),
                limit=self._config.success_per_client_per_min,
                window_s=60,
            )
        )
        issued = self._signer.issue_service(
            client_id=client_id, audience=audience, scopes=scopes, ttl_s=self._config.ttl_s
        )
        log.info(
            "auth.service_token_issued",
            client_id=client_id,
            audience=audience,
            jti=issued.token_id,
        )
        return issued
