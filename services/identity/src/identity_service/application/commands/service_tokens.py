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
from identity_service.application.security import ensure_allowed, hash_secret, rate_key
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
        per_client_per_min: Запросов токена на ``client_id`` в минуту
            (``GV_SERVICE_TOKEN_LIMIT__PER_CLIENT_PER_MIN``).
    """

    ttl_s: int = 600
    per_client_per_min: int = 60


class IssueServiceToken:
    """Выдать сервисный JWT по ``client_id`` и секрету.

    Порядок: лимит по ``client_id`` → секрет → адресат. Неизвестный клиент и неверный
    секрет неразличимы (401 ``auth.client_invalid``); адресат проверяется только после
    успешной аутентификации, поэтому 403 ``auth.audience_forbidden`` не раскрывает
    конфигурацию постороннему.

    Лимит засчитывает каждый запрос (и неудачный): честный клиент обновляет токен раз в
    ``ttl_s / 2`` на реплику и адресата, до лимита ему далеко. Лимитер fail-open (как вход
    паролем): перебор 256-битного секрета невозможен и без него.
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
        self, client_id: str, client_secret: str, audience: str
    ) -> IssuedServiceToken:
        """Проверить учётные данные и выпустить токен.

        Raises:
            RateLimitedError: лимит запросов клиента исчерпан (429).
            UnauthenticatedError: ``auth.client_invalid`` — нет клиента или секрет неверен.
            ForbiddenError: ``auth.audience_forbidden`` — адресат клиенту не разрешён.
        """
        ensure_allowed(
            await self._limiter.hit(
                rate_key("svc_token", client_id),
                limit=self._config.per_client_per_min,
                window_s=60,
            )
        )
        client = self._clients.get(client_id)
        expected = client.secret_sha256 if client is not None else _DUMMY_SHA256
        secret_ok = hmac.compare_digest(hash_secret(client_secret), expected)
        if client is None or not secret_ok:
            log.warning("auth.service_token_denied", client_id=client_id, reason="credentials")
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
