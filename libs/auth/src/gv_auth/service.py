"""Сервисные токены (client credentials, D13 плана этапа 2) — интерфейсы шага 0a.

Вызывающий сервис получает у identity JWT ``POST /internal/v1/service-tokens``
(``aud = <вызываемый сервис>``, ``sub = svc:<client_id>``, ``scope``, TTL 600 с), кэширует
его и обновляет на 50 % TTL (``ServiceTokenProvider``). Вызываемый проверяет токен тем же
JWKS и ``JwtVerifier`` со своим ``audience`` (``ServiceAuthenticator.require_service``).
Пользовательский токен (``aud = guessverse``) internal-ручки не проходит, сервисный —
публичные.

Сигнатуры зафиксированы в 0a, чтобы потоки писали код против них; реализация — шаг 0b
(``docs/plans/stage-2.md`` §2.8).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import Request

from gv_auth.verifier import JwtVerifier

SERVICE_SUBJECT_PREFIX = "svc:"
"""Префикс ``sub`` сервисного токена: ``svc:<client_id>``."""


@dataclass(frozen=True, slots=True)
class ServicePrincipal:
    """Вызывающий сервис, подтверждённый сервисным токеном.

    Attributes:
        client_id: Клиент из ``sub = svc:<client_id>`` (``game``, ``admin``).
        scopes: Разрешённые области (claim ``scope``).
        token_id: ``jti`` токена (для журнала, сам токен не логируется).
    """

    client_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    token_id: str | None = None

    def has_scope(self, scope: str) -> bool:
        """Есть ли область ``scope``."""
        return scope in self.scopes


class ServiceAuthenticator:
    """Зависимости FastAPI для internal-ручек, вызываемых сервисами.

    Args:
        verifier: Верификатор с ``audience`` = имя вызываемого сервиса
            (``GV_INTERNAL_AUTH__AUDIENCE``, по умолчанию ``service_name``).
    """

    def __init__(self, verifier: JwtVerifier) -> None:
        self._verifier = verifier

    def require_service(self, *scopes: str) -> Callable[[Request], Awaitable[ServicePrincipal]]:
        """Зависимость: валидный сервисный токен со ВСЕМИ ``scopes``.

        Нет токена или пользовательский JWT — 401 ``auth.unauthenticated``; нет области —
        403 ``auth.forbidden``.
        """
        if not scopes:
            raise ValueError("укажите хотя бы одну область (scope)")

        async def dependency(request: Request) -> ServicePrincipal:
            raise NotImplementedError("TODO(stage2/0b): проверка сервисного токена")

        return dependency


class ServiceTokenProvider:
    """Клиент сервисных токенов identity для исходящих вызовов.

    Кэширует токен и обновляет его на 50 % TTL; одновременные запросы ждут одно обновление
    (single-flight); при отказе identity отдаёт старый токен до его ``exp``. Секрет и токен
    не логируются.

    Args:
        token_url: ``http://identity:8000/internal/v1/service-tokens``.
        client_id: Идентификатор клиента (``GV_SERVICE_AUTH__CLIENT_ID``).
        secret: Секрет клиента (``GV_SERVICE_AUTH__CLIENT_SECRET``).
        audience: Вызываемый сервис (``media_pipeline``).
        timeout_s: Таймаут запроса к identity.
        refresh_ratio: Доля TTL, после которой токен обновляется.
    """

    def __init__(
        self,
        token_url: str,
        client_id: str,
        secret: str,
        audience: str,
        *,
        timeout_s: float = 2.0,
        refresh_ratio: float = 0.5,
    ) -> None:
        if not 0 < refresh_ratio < 1:
            raise ValueError("refresh_ratio должен быть в (0, 1)")
        self._token_url = token_url
        self._client_id = client_id
        self._secret = secret
        self._audience = audience
        self._timeout_s = timeout_s
        self._refresh_ratio = refresh_ratio

    @property
    def audience(self) -> str:
        """Вызываемый сервис."""
        return self._audience

    async def token(self) -> str:
        """Действующий access-токен для ``Authorization: Bearer``.

        Raises:
            gv_core.errors.DependencyUnavailableError: identity недоступен и кэша нет.
        """
        raise NotImplementedError("TODO(stage2/0b): выдача и кэш сервисного токена")

    async def aclose(self) -> None:
        """Закрыть HTTP-клиент (регистрируется через ``Infrastructure.add_closer``)."""
        raise NotImplementedError("TODO(stage2/0b): закрытие HTTP-клиента")

    def __repr__(self) -> str:
        # Секрет не попадает в repr/логи даже случайно.
        return f"ServiceTokenProvider(client_id={self._client_id!r}, audience={self._audience!r})"
