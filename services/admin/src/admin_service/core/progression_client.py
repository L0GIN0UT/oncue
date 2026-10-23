"""Клиент admin-API progression: версии конфига, ручная выдача и сторно Версов, журнал.

Этап 5a, поток P3 (ADR 0042, 0043). Тонкая обёртка над ``ServiceClient``: пути и тела запросов
в одном месте, ответы — как есть (JSON). Права проверяет progression: ``admin.access`` +
``progression.config.manage|publish``, ``progression.ledger.grant|reverse|read``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote
from uuid import UUID

from admin_service.core.service_client import ServiceClient

PROGRESSION = "progression"
_CONFIGS = "/admin/v1/configs"
_USERS = "/admin/v1/users"


class ProgressionAdminClient:
    """Вызовы ``/admin/v1`` progression от имени вошедшего администратора.

    Args:
        client: Клиент сервиса ``progression`` (токен администратора, ``X-Request-Id``).
    """

    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    def versions(self) -> Any:
        """Версии конфига (новые первыми) → ``{items: [ConfigVersionCard]}``."""
        return self._client.get(_CONFIGS)

    def version(self, version: int) -> Any:
        """Версия с телом → ``{card, config}``."""
        return self._client.get(f"{_CONFIGS}/{int(version)}")

    def save_draft(self, *, config: dict[str, Any], reason: str, idempotency_key: str) -> Any:
        """Сохранить черновик (он один) → ``{card, config}``; невалидный → 422."""
        return self._client.put(
            f"{_CONFIGS}/draft",
            idempotency_key=idempotency_key,
            json={"config": config, "reason": reason},
        )

    def publish(self, *, version: int, body_sha256: str, reason: str, idempotency_key: str) -> Any:
        """Опубликовать черновик (прежняя версия уходит в архив) → ``{card, config}``."""
        return self._client.post(
            f"{_CONFIGS}/draft/publish",
            idempotency_key=idempotency_key,
            json={"version": version, "body_sha256": body_sha256, "reason": reason},
        )

    def rollback(self, version: int, *, reason: str, idempotency_key: str) -> Any:
        """Копия старой версии становится черновиком → ``{card, config}``."""
        return self._client.post(
            f"{_CONFIGS}/{int(version)}/rollback",
            idempotency_key=idempotency_key,
            json={"reason": reason},
        )

    def quests(self) -> Any:
        """Задания действующего конфига → ``{config_version, items}``."""
        return self._client.get("/admin/v1/quests")

    def badges(self) -> Any:
        """Бейджи действующего конфига → ``{config_version, items}``."""
        return self._client.get("/admin/v1/badges")

    def ledger(self, user_id: UUID, *, cursor: str | None = None, limit: int = 50) -> Any:
        """Журнал Версов игрока → ``{user_id, earned, paid, items, next_cursor}``."""
        return self._client.get(
            f"{_USERS}/{user_id}/ledger", params={"cursor": cursor, "limit": limit}
        )

    def grant(self, user_id: UUID, *, amount: int, reason: str, idempotency_key: str) -> Any:
        """Выдать Версы вручную → ``{entry, balance_after}``."""
        return self._client.post(
            f"{_USERS}/{user_id}/ledger/grant",
            idempotency_key=idempotency_key,
            json={"amount": amount, "reason": reason},
        )

    def reverse(self, op_id: str, *, reason: str, idempotency_key: str) -> Any:
        """Сторнировать проводку Версов → ``{entry, balance_after}``."""
        return self._client.post(
            f"/admin/v1/ledger/{quote(op_id, safe='')}/reverse",
            idempotency_key=idempotency_key,
            json={"reason": reason},
        )
