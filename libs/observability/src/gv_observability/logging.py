"""Структурированные логи.

Все сервисы пишут JSON в stdout (12-factor): Filebeat забирает и отправляет в Elasticsearch.
В каждую запись автоматически добавляются ``service``, ``env``, ``correlation_id`` и
``trace_id``, поэтому по одному идентификатору находится вся цепочка через сервисы.

Персональные данные в логи не пишем. Поля с чувствительными именами (см. ``_REDACT_FRAGMENTS``)
маскируются всегда, в том числе во вложенных словарях и в записях стандартного logging.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import trace

from gv_core.context import current_context

# Ключ маскируется, если СОДЕРЖИТ любой из фрагментов (без учёта регистра и разделителей):
# new_password, api_key, client_secret, set-cookie, x-auth-token и т.п.
# Маскировка рекурсивная — заголовки и payload часто логируют вложенными словарями.
_REDACT_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "authorization",
    "cookie",
    "session",
    "otp",
    "card",
    "cvv",
    "iban",
    "email",
    "phone",
    "dsn",
    "databaseurl",
    "privatekey",
)
_MASK = "***"
_MAX_DEPTH = 6


def _is_sensitive(key: str) -> bool:
    normalized = key.lower().replace("_", "").replace("-", "").replace(".", "")
    return any(fragment in normalized for fragment in _REDACT_FRAGMENTS)


def _mask(value: Any, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {
            k: _MASK if isinstance(k, str) and _is_sensitive(k) else _mask(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(_mask(v, depth + 1) for v in value)
    return value


def _add_request_context(
    _: Any, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    ctx = current_context()
    if ctx is not None:
        event_dict.setdefault("correlation_id", str(ctx.correlation_id))
        if ctx.causation_id:
            event_dict.setdefault("causation_id", str(ctx.causation_id))
        if ctx.user_id:
            event_dict.setdefault("user_id", str(ctx.user_id))
    span = trace.get_current_span().get_span_context()
    if span.is_valid:
        event_dict.setdefault("trace_id", format(span.trace_id, "032x"))
        event_dict.setdefault("span_id", format(span.span_id, "016x"))
    return event_dict


def _redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key == "event":
            continue  # текст события — не данные; секреты в строку не форматируем
        event_dict[key] = _MASK if _is_sensitive(key) else _mask(event_dict[key])
    return event_dict


def configure_logging(*, service: str, env: str, level: str = "INFO", json: bool = True) -> None:
    """Настроить structlog и стандартный logging (uvicorn, sqlalchemy, aiokafka).

    Вызывается один раз при старте процесса.
    """
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_request_context,
        _redact,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(service=service, env=env)

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Access-лог uvicorn дублирует метрики и шумит; ошибки uvicorn остаются.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
