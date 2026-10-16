"""Клиент admin-API catalog для разделов этапа 3a: тексты загадок и переопубликация.

Тонкая обёртка над ``ServiceClient`` (ADR 0013, 0024). Ответы — как есть (JSON).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from admin_service.core.service_client import ServiceClient

CATALOG = "catalog"
_ITEMS = "/admin/v1/items"


class CatalogAdminClient:
    """Вызовы ``/admin/v1`` catalog от имени вошедшего модератора.

    Args:
        client: Клиент сервиса ``catalog``.
    """

    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    def clue_texts(self, item_id: UUID) -> Any:
        """Тексты загадок единицы (``admin.access``)."""
        return self._client.get(f"{_ITEMS}/{item_id}/clue-texts")

    def save_clue_texts(
        self,
        item_id: UUID,
        *,
        plot_masked: str | None,
        tagline: str | None,
        emoji: str | None,
        base_revision: int,
        reason: str,
        idempotency_key: str,
    ) -> Any:
        """Заменить тексты целиком (``None`` — очистить); после правки — снова ``draft``.

        Устаревшая ``base_revision`` → 409 ``catalog.clue_texts_revision_mismatch``.
        """
        return self._client.put(
            f"{_ITEMS}/{item_id}/clue-texts",
            idempotency_key=idempotency_key,
            json={
                "plot_masked": plot_masked,
                "tagline": tagline,
                "emoji": emoji,
                "base_revision": base_revision,
                "reason": reason,
            },
        )

    def approve_clue_texts(
        self, item_id: UUID, *, revision: int, reason: str, idempotency_key: str
    ) -> Any:
        """Одобрить тексты ревизии ``revision`` (уходят в ``MediaItemUpdated``)."""
        return self._client.post(
            f"{_ITEMS}/{item_id}/clue-texts/approve",
            idempotency_key=idempotency_key,
            json={"revision": revision, "reason": reason},
        )

    def republish(self, *, domain: str | None, reason: str, idempotency_key: str) -> Any:
        """Переопубликовать единицы домена (без домена — все) → ``{job_id}`` (202)."""
        return self._client.post(
            f"{_ITEMS}/republish",
            idempotency_key=idempotency_key,
            json={"domain": domain, "reason": reason},
        )
