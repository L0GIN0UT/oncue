"""Сервисные токены для исходящих вызовов (client credentials, D13 плана этапа 2).

Сервис, которому нужно вызвать internal-ручку другого сервиса, собирает провайдер так::

    media_tokens = build_service_token_provider(infra, audience="media_pipeline")
    headers = await media_tokens.headers()

Учётные данные — ``GV_SERVICE_AUTH__*`` (``ServiceAuthSettings``). HTTP-клиент провайдера
закрывается вместе с процессом (``Infrastructure.add_closer``).
"""

from __future__ import annotations

from prometheus_client import Counter

from gv_auth.service import ServiceTokenProvider
from gv_service.definition import Infrastructure

SERVICE_TOKEN_REFRESH_FAILURES = Counter(
    "gv_service_token_refresh_failures_total",
    "Неудачные запросы сервисного токена у identity",
    ["audience"],
)


class ServiceAuthNotConfiguredError(RuntimeError):
    """Не заданы ``GV_SERVICE_AUTH__TOKEN_URL`` или ``GV_SERVICE_AUTH__CLIENT_SECRET``."""


def build_service_token_provider(infra: Infrastructure, audience: str) -> ServiceTokenProvider:
    """Провайдер токенов для вызовов сервиса ``audience``.

    Raises:
        ServiceAuthNotConfiguredError: нет адреса identity или секрета клиента.
    """
    settings = infra.settings
    auth = settings.service_auth
    if not auth.token_url or auth.client_secret is None:
        raise ServiceAuthNotConfiguredError(
            "для вызовов internal-ручек задайте GV_SERVICE_AUTH__TOKEN_URL и "
            "GV_SERVICE_AUTH__CLIENT_SECRET"
        )
    failures = SERVICE_TOKEN_REFRESH_FAILURES.labels(audience)
    provider = ServiceTokenProvider(
        auth.token_url,
        auth.client_id or settings.service_name,
        auth.client_secret.get_secret_value(),
        audience,
        timeout_s=auth.timeout_s,
        refresh_ratio=auth.refresh_ratio,
        retry_backoff_s=auth.retry_backoff_s,
        on_refresh_failure=failures.inc,
    )
    infra.add_closer(provider.aclose)
    return provider
