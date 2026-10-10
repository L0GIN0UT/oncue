"""Клиент admin/internal API сервисов (ADR 0013: админка читает и пишет только через API).

Каждый вызов несёт токен вошедшего модератора (сервис сам проверяет права и пишет свой
аудит с ``actor_id``), ``X-Request-Id`` (связь с логами) и для мутаций ``Idempotency-Key``.
Ошибки сервиса (RFC 9457 Problem Details) превращаются в ``ServiceError`` с понятным
человеку сообщением; машинный ``code`` сохраняется для аудита.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Сообщения для людей по машинному коду ошибки. Неизвестный код — detail/title ответа.
_MESSAGES: Mapping[str, str] = {
    "auth.unauthenticated": "Сессия истекла — войдите заново",
    "auth.token_expired": "Сессия истекла — войдите заново",
    "auth.token_invalid": "Сессия недействительна — войдите заново",
    "auth.forbidden": "Недостаточно прав для этого действия",
    "resource.not_found": "Объект не найден",
    "resource.conflict": "Данные изменились — обновите страницу и повторите",
    "request.validation_failed": "Сервис отклонил данные формы",
    "rate.limited": "Слишком много запросов — повторите позже",
    "feature.disabled": "Функция выключена в настройках сервиса",
    "dependency.unavailable": "Сервис временно недоступен",
    "idempotency.key_reused": "Форма уже отправлялась с другими данными — обновите страницу",
    # game (docs/plans/stage-2.md §2.6)
    "game.draft_conflict": (
        "Черновик уже изменил кто-то другой — ваша правка не сохранена, сравните её с текущей"
    ),
    "game.config_invalid": "Конфиг не прошёл проверку — исправьте ошибки и повторите",
    "game.mode_not_found": "Режим не найден",
    "game.daily_locked": "Набор дня уже заблокирован (день начался) — менять его нельзя",
    "game.item_not_playable": "У этой единицы нет готовых ассетов под режим",
    "game.item_duplicate": "Эта единица уже есть в наборе дня",
}


class ServiceError(Exception):
    """Отказ или недоступность сервиса.

    Attributes:
        service: Имя сервиса.
        status: HTTP-статус; ``None`` — ответа нет (сеть, таймаут).
        code: Машинный код (``code`` из Problem Details или ``service.unavailable``).
        message: Сообщение для модератора.
    """

    def __init__(self, service: str, status: int | None, code: str, message: str) -> None:
        super().__init__(f"{service}: {code}")
        self.service = service
        self.status = status
        self.code = code
        self.message = message


class SessionExpiredError(Exception):
    """Токены identity недействительны: нужен повторный вход."""


def problem_to_error(service: str, response: httpx.Response) -> ServiceError:
    """Ответ с ошибкой → ``ServiceError``. Тело не Problem Details — по статусу."""
    body: dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            body = parsed
    except ValueError:
        pass
    code = str(body.get("code") or f"http.{response.status_code}")
    message = _MESSAGES.get(code) or str(
        body.get("detail") or body.get("title") or f"Ошибка сервиса {service}"
    )
    errors = body.get("errors")
    if code == "request.validation_failed" and isinstance(errors, list) and errors:
        details = "; ".join(
            f"{'.'.join(map(str, e.get('loc', [])))}: {e.get('msg', '')}"
            for e in errors[:5]
            if isinstance(e, dict)
        )
        message = f"{message}: {details}"
    return ServiceError(service, response.status_code, code, message)


class ServiceClient:
    """Синхронный клиент одного сервиса на время одного запроса админки.

    Args:
        service: Имя сервиса (для сообщений и логов).
        base_url: Базовый URL сервиса во внутренней сети.
        access_token: Access JWT вошедшего пользователя.
        request_id: Сквозной id запроса.
        timeout_s: Таймаут вызова.
        transport: Транспорт httpx (тесты).
    """

    def __init__(
        self,
        service: str,
        *,
        base_url: str,
        access_token: str,
        request_id: str,
        timeout_s: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.service = service
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {access_token}", "X-Request-Id": request_id},
            follow_redirects=False,
        )

    def close(self) -> None:
        """Закрыть соединения."""
        self._client.close()

    def __enter__(self) -> ServiceClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        """GET; пустые параметры не передаются."""
        clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        return self._send("GET", path, params=clean)

    def post(
        self,
        path: str,
        *,
        idempotency_key: str,
        json: Any = None,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, Any, str]] | None = None,
    ) -> Any:
        """POST-мутация (JSON или multipart)."""
        return self._send(
            "POST", path, idempotency_key=idempotency_key, json=json, data=data, files=files
        )

    def query(self, path: str, *, json: Any) -> Any:
        """POST без побочных эффектов (проверка конфига): без ``Idempotency-Key``.

        Отдельный метод, а не ``post`` с пустым ключом: так мутация без ключа по-прежнему
        остаётся ошибкой программиста, а «безопасный» POST виден в коде явно.
        """
        return self._send("POST", path, safe=True, json=json)

    def put(self, path: str, *, idempotency_key: str, json: Any) -> Any:
        """PUT-мутация."""
        return self._send("PUT", path, idempotency_key=idempotency_key, json=json)

    def _send(
        self,
        method: str,
        path: str,
        *,
        idempotency_key: str | None = None,
        safe: bool = False,
        **kwargs: Any,
    ) -> Any:
        headers: dict[str, str] = {}
        if method in MUTATING_METHODS and not safe:
            if not idempotency_key:
                raise ValueError("мутация без Idempotency-Key")
            headers["Idempotency-Key"] = idempotency_key
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        try:
            response = self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            log.warning("service.unavailable", service=self.service, method=method, error=repr(exc))
            raise ServiceError(
                self.service, None, "service.unavailable", f"Сервис {self.service} недоступен"
            ) from exc
        if response.is_error:
            error = problem_to_error(self.service, response)
            log.info(
                "service.rejected",
                service=self.service,
                method=method,
                status=response.status_code,
                code=error.code,
            )
            raise error
        if response.status_code == httpx.codes.NO_CONTENT or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ServiceError(
                self.service, response.status_code, "service.bad_response", "Некорректный ответ"
            ) from exc
