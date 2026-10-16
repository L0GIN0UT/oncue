"""Клиент admin-API collections: модерация и кураторские сборники (план этапа 3b §2.5, D14).

Тонкая обёртка над ``ServiceClient``: пути, параметры и тела запросов в одном месте. Ответы —
как есть (JSON), их форму проверяют представления и шаблоны, устойчивые к неожиданным данным.
Права проверяет collections: ``admin.access`` + ``collections.view|curate|unpublish|
restrict_author``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from admin_service.core.service_client import ServiceClient

COLLECTIONS = "collections"
_COLLECTIONS = "/admin/v1/collections"
_AUTHORS = "/admin/v1/authors"


class CollectionsAdminClient:
    """Вызовы ``/admin/v1`` collections от имени вошедшего модератора.

    Args:
        client: Клиент сервиса ``collections`` (токен модератора, ``X-Request-Id``).
    """

    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    # ------------------------------------------------------------------ чтение
    def collections(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> Any:
        """Страница сборников, включая черновики и скрытые (``collections.view``)."""
        return self._client.get(
            _COLLECTIONS,
            params={"q": q, "status": status, "kind": kind, "cursor": cursor, "limit": limit},
        )

    def collection(self, collection_id: UUID) -> Any:
        """Карточка с составом и своими эмодзи автора (``collections.view``)."""
        return self._client.get(f"{_COLLECTIONS}/{collection_id}")

    # ------------------------------------------------------------------ кураторские
    def create_curated(self, body: dict[str, Any], *, reason: str, idempotency_key: str) -> Any:
        """Создать и сразу опубликовать кураторский сборник (``collections.curate``).

        422 ``collections.publish_invalid`` — ``details.errors`` ``[{path, code}]``.
        """
        return self._client.post(
            _COLLECTIONS, idempotency_key=idempotency_key, json={**body, "reason": reason}
        )

    def update_curated(
        self,
        collection_id: UUID,
        body: dict[str, Any],
        *,
        base_version: int,
        reason: str,
        idempotency_key: str,
    ) -> Any:
        """Новая версия кураторского сборника; устаревшая ``base_version`` — 409."""
        return self._client.put(
            f"{_COLLECTIONS}/{collection_id}",
            idempotency_key=idempotency_key,
            json={**body, "base_version": base_version, "reason": reason},
        )

    # ------------------------------------------------------------------ модерация
    def set_hidden(
        self, collection_id: UUID, *, hidden: bool, reason: str, idempotency_key: str
    ) -> Any:
        """Скрыть или показать сборник (``collections.unpublish``)."""
        action = "hide" if hidden else "unhide"
        return self._client.post(
            f"{_COLLECTIONS}/{collection_id}/{action}",
            idempotency_key=idempotency_key,
            json={"reason": reason},
        )

    def set_featured(
        self,
        collection_id: UUID,
        *,
        featured: bool,
        rank: int | None,
        reason: str,
        idempotency_key: str,
    ) -> Any:
        """Отметка «рекомендуемое» и порядок в блоке (``collections.curate``)."""
        return self._client.put(
            f"{_COLLECTIONS}/{collection_id}/featured",
            idempotency_key=idempotency_key,
            json={"featured": featured, "rank": rank if featured else None, "reason": reason},
        )

    def set_restriction(
        self, user_id: UUID, *, restricted: bool, reason: str, idempotency_key: str
    ) -> Any:
        """Ограничить автора или снять ограничение (``collections.restrict_author``)."""
        path = f"{_AUTHORS}/{user_id}/restriction"
        body = {"reason": reason}
        if restricted:
            return self._client.post(path, idempotency_key=idempotency_key, json=body)
        return self._client.delete(path, idempotency_key=idempotency_key, json=body)
