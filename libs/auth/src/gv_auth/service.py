"""Сервисные токены (client credentials, D13 плана этапа 2).

Вызывающий сервис получает у identity JWT ``POST /internal/v1/service-tokens``
(``aud = <вызываемый сервис>``, ``sub = svc:<client_id>``, ``scope``, TTL 600 с), кэширует
его и обновляет на 50 % TTL (``ServiceTokenProvider``). Вызываемый проверяет токен тем же
JWKS и ``JwtVerifier`` со своим ``audience`` (``ServiceAuthenticator.require_service``).

Граница между пользовательскими и сервисными токенами держится на двух независимых
проверках: ``aud`` (пользовательский — ``guessverse``, сервисный — имя вызываемого
сервиса) и форма ``sub`` (UUID против ``svc:<client_id>``). Пользовательский токен
internal-ручки не проходит, сервисный — публичные.

Ни секрет клиента, ни токен не логируются и не попадают в ``repr``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import Request

from gv_auth.verifier import JwtVerifier
from gv_core.errors import DependencyUnavailableError, ForbiddenError, UnauthenticatedError

# stdlib logging: gv_auth не зависит от structlog (см. verifier.py).
log = logging.getLogger(__name__)

SERVICE_SUBJECT_PREFIX = "svc:"
"""Префикс ``sub`` сервисного токена: ``svc:<client_id>``."""

CLIENT_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
"""Допустимый ``client_id`` (совпадает со схемой ``POST /internal/v1/service-tokens``)."""

SCOPE_PATTERN = r"^[a-z][a-z0-9_.:-]{0,63}$"
"""Допустимая область (``assets.link``, ``admin_sessions``)."""

_CLIENT_ID = re.compile(CLIENT_ID_PATTERN)
_SCOPE = re.compile(SCOPE_PATTERN)
_BEARER = "bearer "

# Claims пользовательского токена. В сервисном их не бывает: identity их не выпускает, а
# их наличие означает подделку или ошибку конфигурации — такой токен не принимаем.
_USER_CLAIMS = ("sid", "roles", "perms", "gst")


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


def parse_scope(value: object) -> frozenset[str]:
    """Claim ``scope`` (области через пробел, RFC 9068 §2.2.3) → множество областей.

    Нестроковое значение и некорректные элементы отбрасываются: лишних прав из мусора
    не возникает.
    """
    if not isinstance(value, str):
        return frozenset()
    return frozenset(s for s in value.split(" ") if _SCOPE.match(s))


def service_principal_from_claims(claims: Mapping[str, Any]) -> ServicePrincipal:
    """Проверенные claims → ``ServicePrincipal``.

    Raises:
        UnauthenticatedError: ``auth.token_invalid`` — это не сервисный токен.
    """
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.startswith(SERVICE_SUBJECT_PREFIX):
        raise UnauthenticatedError("нужен сервисный токен", code="auth.token_invalid")
    client_id = sub[len(SERVICE_SUBJECT_PREFIX) :]
    if not _CLIENT_ID.match(client_id):
        raise UnauthenticatedError("некорректный клиент в токене", code="auth.token_invalid")
    if any(name in claims for name in _USER_CLAIMS):
        raise UnauthenticatedError("нужен сервисный токен", code="auth.token_invalid")
    jti = claims.get("jti")
    return ServicePrincipal(
        client_id=client_id,
        scopes=parse_scope(claims.get("scope")),
        token_id=jti if isinstance(jti, str) else None,
    )


def _bearer(request: Request) -> str | None:
    # Только заголовок: cookie ставит браузер пользователя, сервисам она не нужна, а
    # приём cookie на internal-ручках открыл бы их для CSRF.
    header = request.headers.get("authorization")
    if header and header.lower().startswith(_BEARER):
        token = header[len(_BEARER) :].strip()
        return token or None
    return None


class ServiceAuthenticator:
    """Зависимости FastAPI для internal-ручек, вызываемых сервисами.

    Args:
        verifier: Верификатор с ``audience`` = имя вызываемого сервиса
            (``GV_INTERNAL_AUTH__AUDIENCE``, по умолчанию ``service_name``); ``None`` — брать
            ``app.state.service_jwt_verifier`` (его создаёт ``gv_service.create_app``).
    """

    def __init__(self, verifier: JwtVerifier | None = None) -> None:
        self._verifier = verifier

    def _get_verifier(self, request: Request) -> JwtVerifier:
        if self._verifier is not None:
            return self._verifier
        verifier = getattr(request.app.state, "service_jwt_verifier", None)
        if not isinstance(verifier, JwtVerifier):
            # Ошибка конфигурации (нет GV_JWKS_URL): закрыто, а не открыто.
            raise RuntimeError("app.state.service_jwt_verifier не настроен")  # noqa: TRY004
        return verifier

    async def authenticate(self, request: Request) -> ServicePrincipal:
        """Сервис-отправитель запроса.

        Raises:
            UnauthenticatedError: нет токена, токен не для этого сервиса или не сервисный.
            DependencyUnavailableError: ключи подписи недоступны, а кэша нет.
        """
        token = _bearer(request)
        if token is None:
            raise UnauthenticatedError("требуется сервисный токен")
        claims = await self._get_verifier(request).decode(token)
        return service_principal_from_claims(claims)

    def require_service(self, *scopes: str) -> Callable[[Request], Awaitable[ServicePrincipal]]:
        """Зависимость: валидный сервисный токен со ВСЕМИ ``scopes``.

        Нет токена или пользовательский JWT — 401 ``auth.unauthenticated``/``auth.token_*``;
        нет области — 403 ``auth.forbidden``.
        """
        if not scopes:
            raise ValueError("укажите хотя бы одну область (scope)")
        required = frozenset(scopes)

        async def dependency(request: Request) -> ServicePrincipal:
            principal = await self.authenticate(request)
            if not required <= principal.scopes:
                log.warning(
                    "auth.service_scope_denied client_id=%s required=%s",
                    principal.client_id,
                    ",".join(sorted(required)),
                )
                raise ForbiddenError("недостаточно прав сервиса", code="auth.forbidden")
            return principal

        return dependency


# ----------------------------------------------------------------------------- выдача


def _parse_token_response(response: httpx.Response) -> tuple[str, int]:
    """Ответ identity → (токен, ``expires_in``).

    Raises:
        DependencyUnavailableError: identity ответил ошибкой или неожиданной формой.
    """
    if response.status_code != 200:  # noqa: PLR2004
        raise DependencyUnavailableError(
            "identity отказал в сервисном токене",
            code="auth.service_token_unavailable",
            details={"status": response.status_code},
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise DependencyUnavailableError(
            "некорректный ответ identity", code="auth.service_token_unavailable"
        ) from exc
    token = body.get("access_token") if isinstance(body, dict) else None
    expires_in = body.get("expires_in") if isinstance(body, dict) else None
    token_type = body.get("token_type") if isinstance(body, dict) else None
    if (
        not isinstance(token, str)
        or not token
        or not isinstance(expires_in, int)
        or isinstance(expires_in, bool)
        or expires_in <= 0
        or not isinstance(token_type, str)
        or token_type.lower() != "bearer"
    ):
        raise DependencyUnavailableError(
            "некорректный ответ identity", code="auth.service_token_unavailable"
        )
    return token, expires_in


class _TokenCache:
    """Состояние кэша токена без IO и без блокировок (общая логика async/sync клиентов).

    Время — монотонные секунды вызывающего: ``exp`` из JWT не читается, срок считается от
    момента ОТПРАВКИ запроса и ``expires_in``, поэтому расхождение часов с identity не важно.
    """

    def __init__(self, refresh_ratio: float, retry_backoff_s: float, expiry_margin_s: float):
        if not 0 < refresh_ratio < 1:
            raise ValueError("refresh_ratio должен быть в (0, 1)")
        if retry_backoff_s < 0 or expiry_margin_s < 0:
            raise ValueError("retry_backoff_s и expiry_margin_s не могут быть отрицательными")
        self._ratio = refresh_ratio
        self._backoff = retry_backoff_s
        self._margin = expiry_margin_s
        self._value: str | None = None
        self._refresh_at = 0.0
        self._expires_at = 0.0
        self._retry_at = 0.0

    def _valid(self, now: float) -> bool:
        return self._value is not None and now < self._expires_at - self._margin

    def cached(self, now: float) -> str | None:
        """Токен, который можно отдать без запроса в identity, иначе ``None``.

        Свежий (до точки обновления) — всегда; устаревающий — только пока действует пауза
        после неудачного обновления: иначе при лежащем identity каждый вызов ждал бы таймаут.
        """
        if self._value is not None and now < self._refresh_at:
            return self._value
        if self._valid(now) and now < self._retry_at:
            return self._value
        return None

    def in_backoff(self, now: float) -> bool:
        """Идёт пауза после неудачи, а живого токена нет: в identity не ходим, сразу отказ.

        Иначе при лежащем identity каждый ожидающий блокировку повторял бы запрос по очереди.
        """
        return now < self._retry_at

    def store(self, started_at: float, token: str, expires_in: int) -> str:
        """Запомнить новый токен; срок отсчитывается от отправки запроса."""
        self._value = token
        self._refresh_at = started_at + expires_in * self._ratio
        self._expires_at = started_at + expires_in
        self._retry_at = 0.0
        return token

    def failed(self, now: float) -> str | None:
        """Обновление не удалось: пауза до следующей попытки; старый токен, если ещё жив."""
        self._retry_at = now + self._backoff
        return self._value if self._valid(now) else None

    def clear(self) -> None:
        """Забыть токен (например, вызываемый сервис ответил 401)."""
        self._value = None
        self._refresh_at = self._expires_at = self._retry_at = 0.0


@dataclass(frozen=True, slots=True)
class _Request:
    url: str
    body: dict[str, str]
    client_id: str
    audience: str


def _on_failure(
    request: _Request, exc: Exception, *, kept_old: bool, hook: Callable[[], None] | None
) -> None:
    # В лог — только клиент, адресат и тип ошибки: ни секрета, ни токена.
    log.warning(
        "auth.service_token_refresh_failed client_id=%s audience=%s error=%s status=%s kept_old=%s",
        request.client_id,
        request.audience,
        type(exc).__name__,
        exc.details.get("status") if isinstance(exc, DependencyUnavailableError) else None,
        kept_old,
    )
    if hook is not None:
        hook()


def _unavailable(exc: Exception) -> DependencyUnavailableError:
    if isinstance(exc, DependencyUnavailableError):
        return exc
    return DependencyUnavailableError("identity недоступен", code="auth.service_token_unavailable")


def _backoff_error() -> DependencyUnavailableError:
    return DependencyUnavailableError(
        "identity недоступен, повтор позже", code="auth.service_token_unavailable"
    )


class ServiceTokenProvider:
    """Клиент сервисных токенов identity для исходящих вызовов (asyncio).

    Кэширует токен и обновляет его на ``refresh_ratio`` TTL; одновременные запросы ждут одно
    обновление (single-flight). При отказе identity отдаёт старый токен до его истечения и
    не повторяет запрос чаще ``retry_backoff_s``. Секрет и токен не логируются.

    Args:
        token_url: ``http://identity:8000/internal/v1/service-tokens``.
        client_id: Идентификатор клиента (``GV_SERVICE_AUTH__CLIENT_ID``).
        secret: Секрет клиента (``GV_SERVICE_AUTH__CLIENT_SECRET``).
        audience: Вызываемый сервис (``media_pipeline``).
        timeout_s: Таймаут запроса к identity.
        refresh_ratio: Доля TTL, после которой токен обновляется.
        retry_backoff_s: Пауза после неудачного обновления, пока жив старый токен.
        expiry_margin_s: Запас до истечения: токен, которому осталось меньше, не отдаём.
        transport: Транспорт httpx (тесты).
        clock: Монотонные часы в секундах (тесты).
        on_refresh_failure: Вызывается при каждой неудаче (метрика сервиса
            ``service_token_refresh_failures_total``).
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
        retry_backoff_s: float = 5.0,
        expiry_margin_s: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_refresh_failure: Callable[[], None] | None = None,
    ) -> None:
        self._cache = _TokenCache(refresh_ratio, retry_backoff_s, expiry_margin_s)
        self._client_id = client_id
        self._audience = audience
        self._request = _Request(
            url=token_url,
            body={"client_id": client_id, "client_secret": secret, "audience": audience},
            client_id=client_id,
            audience=audience,
        )
        self._timeout_s = timeout_s
        self._transport = transport
        self._clock = clock
        self._on_refresh_failure = on_refresh_failure
        self._http: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    @property
    def audience(self) -> str:
        """Вызываемый сервис."""
        return self._audience

    async def token(self) -> str:
        """Действующий access-токен для ``Authorization: Bearer``.

        Raises:
            gv_core.errors.DependencyUnavailableError: identity недоступен и кэша нет.
        """
        cached = self._cache.cached(self._clock())
        if cached is not None:
            return cached
        async with self._lock:
            # Пока ждали блокировку, обновление мог выполнить другой запрос (single-flight).
            cached = self._cache.cached(self._clock())
            if cached is not None:
                return cached
            started = self._clock()
            if self._cache.in_backoff(started):
                raise _backoff_error()
            try:
                token, expires_in = await self._fetch()
            except Exception as exc:
                old = self._cache.failed(self._clock())
                _on_failure(
                    self._request, exc, kept_old=old is not None, hook=self._on_refresh_failure
                )
                if old is not None:
                    return old
                raise _unavailable(exc) from exc
            return self._cache.store(started, token, expires_in)

    async def headers(self) -> dict[str, str]:
        """Заголовок ``Authorization`` для исходящего запроса."""
        return {"Authorization": f"Bearer {await self.token()}"}

    def invalidate(self) -> None:
        """Забыть токен: следующий ``token()`` запросит новый (после 401 от вызываемого)."""
        self._cache.clear()

    async def _fetch(self) -> tuple[str, int]:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport)
        response = await self._http.post(self._request.url, json=self._request.body)
        return _parse_token_response(response)

    async def aclose(self) -> None:
        """Закрыть HTTP-клиент (регистрируется через ``Infrastructure.add_closer``)."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def __repr__(self) -> str:
        # Секрет не попадает в repr/логи даже случайно.
        return f"ServiceTokenProvider(client_id={self._client_id!r}, audience={self._audience!r})"


class SyncServiceTokenProvider:
    """То же, что ``ServiceTokenProvider``, для синхронного кода (admin на Django).

    Потокобезопасен: потоки сервера ждут одно обновление (single-flight под
    ``threading.Lock``). Аргументы — как у ``ServiceTokenProvider``.
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
        retry_backoff_s: float = 5.0,
        expiry_margin_s: float = 5.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_refresh_failure: Callable[[], None] | None = None,
    ) -> None:
        self._cache = _TokenCache(refresh_ratio, retry_backoff_s, expiry_margin_s)
        self._client_id = client_id
        self._audience = audience
        self._request = _Request(
            url=token_url,
            body={"client_id": client_id, "client_secret": secret, "audience": audience},
            client_id=client_id,
            audience=audience,
        )
        self._timeout_s = timeout_s
        self._transport = transport
        self._clock = clock
        self._on_refresh_failure = on_refresh_failure
        self._http: httpx.Client | None = None
        self._lock = threading.Lock()

    @property
    def audience(self) -> str:
        """Вызываемый сервис."""
        return self._audience

    def token(self) -> str:
        """Действующий access-токен (см. ``ServiceTokenProvider.token``)."""
        cached = self._cache.cached(self._clock())
        if cached is not None:
            return cached
        with self._lock:
            cached = self._cache.cached(self._clock())
            if cached is not None:
                return cached
            started = self._clock()
            if self._cache.in_backoff(started):
                raise _backoff_error()
            try:
                token, expires_in = self._fetch()
            except Exception as exc:
                old = self._cache.failed(self._clock())
                _on_failure(
                    self._request, exc, kept_old=old is not None, hook=self._on_refresh_failure
                )
                if old is not None:
                    return old
                raise _unavailable(exc) from exc
            return self._cache.store(started, token, expires_in)

    def headers(self) -> dict[str, str]:
        """Заголовок ``Authorization`` для исходящего запроса."""
        return {"Authorization": f"Bearer {self.token()}"}

    def invalidate(self) -> None:
        """Забыть токен: следующий ``token()`` запросит новый."""
        with self._lock:
            self._cache.clear()

    def _fetch(self) -> tuple[str, int]:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout_s, transport=self._transport)
        response = self._http.post(self._request.url, json=self._request.body)
        return _parse_token_response(response)

    def close(self) -> None:
        """Закрыть HTTP-клиент."""
        if self._http is not None:
            self._http.close()
            self._http = None

    def __repr__(self) -> str:
        return (
            f"SyncServiceTokenProvider(client_id={self._client_id!r}, audience={self._audience!r})"
        )
