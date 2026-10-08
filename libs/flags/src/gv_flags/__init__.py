"""gv_flags — фича-флаги.

Код спрашивает флаг через ``FlagClient`` и всегда передаёт значение по умолчанию: если
Unleash недоступен, сервис продолжает работать на безопасных умолчаниях (например,
``billing.enabled`` → ``False``).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FlagContext:
    """Контекст вычисления флага (для процентных раскаток и таргетинга)."""

    user_id: str | None = None
    roles: frozenset[str] = field(default_factory=frozenset)
    properties: Mapping[str, str] = field(default_factory=dict)


class FlagClient(Protocol):
    """Источник значений флагов."""

    def is_enabled(self, name: str, context: FlagContext | None = None, *, default: bool) -> bool:
        """Включён ли флаг ``name``."""
        ...


class StaticFlagClient:
    """Флаги из словаря: локальная разработка и тесты."""

    def __init__(self, flags: Mapping[str, bool] | None = None) -> None:
        self._flags = dict(flags or {})

    def set(self, name: str, *, enabled: bool) -> None:
        """Переключить флаг (в тестах)."""
        self._flags[name] = enabled

    def is_enabled(
        self,
        name: str,
        context: FlagContext | None = None,  # noqa: ARG002 — сигнатура протокола
        *,
        default: bool,
    ) -> bool:
        """Значение из словаря или ``default``."""
        return self._flags.get(name, default)


class UnleashFlagClient:
    """Адаптер Unleash. SDK держит локальный кэш и опрашивает сервер в фоне."""

    def __init__(self, client: Any) -> None:
        """Принимает уже созданный ``UnleashClient`` (см. ``create_unleash_client``)."""
        self._client = client

    def is_enabled(self, name: str, context: FlagContext | None = None, *, default: bool) -> bool:
        """Значение флага; при любой ошибке SDK — ``default``."""
        ctx = context or FlagContext()
        unleash_ctx: dict[str, Any] = {
            "properties": {**ctx.properties, "roles": ",".join(sorted(ctx.roles))}
        }
        if ctx.user_id:
            unleash_ctx["userId"] = ctx.user_id
        try:
            return bool(
                self._client.is_enabled(name, unleash_ctx, fallback_function=lambda *_: default)
            )
        except Exception:
            log.warning("flags.evaluation_failed", extra={"flag": name}, exc_info=True)
            return default


def create_unleash_client(
    *, url: str, app_name: str, environment: str, api_token: str
) -> UnleashFlagClient:
    """Создать и запустить клиент Unleash."""
    from UnleashClient import UnleashClient

    client = UnleashClient(
        url=url,
        app_name=app_name,
        environment=environment,
        custom_headers={"Authorization": api_token},
        refresh_interval=15,
        disable_metrics=False,
    )
    client.initialize_client()
    return UnleashFlagClient(client)


__all__ = [
    "FlagClient",
    "FlagContext",
    "StaticFlagClient",
    "UnleashFlagClient",
    "create_unleash_client",
]
