"""Клиент admin-API notifications: рассылки (этап 5b, право ``notifications.broadcast.send``)."""

from __future__ import annotations

from typing import Any

from admin_service.core.service_client import ServiceClient

NOTIFICATIONS = "notifications"
_BROADCASTS = "/admin/v1/broadcasts"


class NotificationsAdminClient:
    """Вызовы ``/admin/v1`` notifications от имени вошедшего администратора."""

    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    def broadcasts(self, *, cursor: str | None = None, limit: int = 30) -> Any:
        """Рассылки → ``{items, next_cursor}``."""
        return self._client.get(_BROADCASTS, params={"cursor": cursor, "limit": limit})

    def create_broadcast(
        self, *, subject: str, body: str, reason: str, idempotency_key: str
    ) -> Any:
        """Поставить рассылку в очередь (202) → ``Broadcast``."""
        return self._client.post(
            _BROADCASTS,
            idempotency_key=idempotency_key,
            json={"subject": subject, "body": body, "reason": reason},
        )
