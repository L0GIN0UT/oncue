"""Клиент admin-API match: активные комнаты, принудительное завершение, итог матча.

План этапа 4 §2.5, §10 (4a). Тонкая обёртка над ``ServiceClient``: пути, параметры и тела
запросов в одном месте. Ответы — как есть (JSON), их форму проверяют представления и шаблоны,
устойчивые к неожиданным данным. Права проверяет match: ``admin.access`` + ``match.manage``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from admin_service.core.service_client import ServiceClient

MATCH = "match"
_ROOMS = "/admin/v1/rooms"
_MATCHES = "/admin/v1/matches"


class MatchAdminClient:
    """Вызовы ``/admin/v1`` match от имени вошедшего администратора.

    Args:
        client: Клиент сервиса ``match`` (токен администратора, ``X-Request-Id``).
    """

    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    def rooms(
        self, *, phase: str | None = None, cursor: str | None = None, limit: int | None = None
    ) -> Any:
        """Страница активных комнат (любая фаза, кроме ``closed``) → ``{items, next_cursor}``."""
        return self._client.get(_ROOMS, params={"phase": phase, "cursor": cursor, "limit": limit})

    def abort(self, code: str, *, reason: str, idempotency_key: str) -> Any:
        """Принудительно завершить комнату → 202 ``{accepted, version}``.

        Команда уходит владельцу комнаты (ADR 0034): матч завершается с ``finish = aborted``.
        Ошибки: 404 ``match.room_not_found``, 409 ``match.room_closed``.
        """
        # Код уже проверен конвертером маршрута (алфавит без спецсимволов): quote не нужен.
        return self._client.post(
            f"{_ROOMS}/{code}/abort", idempotency_key=idempotency_key, json={"reason": reason}
        )

    def match(self, match_id: UUID) -> Any:
        """Итог матча (``MatchResult``); нет матча — 404 ``match.match_not_found``."""
        return self._client.get(f"{_MATCHES}/{match_id}")
