"""Клиент admin-API rating: ladder-ы, версии, черновик, публикация, откат, сезоны, лидерборд.

Этап 4b (ADR 0037, 0039). Тонкая обёртка над ``ServiceClient``: пути и тела запросов в одном
месте. Ответы — как есть (JSON). Права проверяет rating: ``admin.access`` +
``rating.ladders.publish`` (ladder-ы) или ``rating.seasons.manage`` (сезоны, пересборка).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from admin_service.core.service_client import ServiceClient

RATING = "rating"
_LADDERS = "/admin/v1/ladders"
_SEASONS = "/admin/v1/seasons"


def _card(body: Any) -> Any:
    """``LadderVersionBody`` → его ``card`` (версия, статус): тело конфига вызывающим не нужно."""
    return body.get("card") if isinstance(body, dict) else body


class RatingAdminClient:
    """Вызовы ``/admin/v1`` rating от имени вошедшего администратора.

    Args:
        client: Клиент сервиса ``rating`` (токен администратора, ``X-Request-Id``).
    """

    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    def ladders(self) -> Any:
        """Все ladder-ы → ``{items: [{key, published_version, draft_version, season_id}]}``."""
        return self._client.get(_LADDERS)

    def versions(self, ladder: str) -> Any:
        """Версии ladder-а (новые первыми) → ``{items: [LadderVersionCard]}``."""
        return self._client.get(f"{_LADDERS}/{ladder}/versions")

    def version(self, ladder: str, version: int) -> Any:
        """Версия с конфигом → ``{card, config}``."""
        return self._client.get(f"{_LADDERS}/{ladder}/versions/{int(version)}")

    def save_draft(
        self, ladder: str, *, config: dict[str, Any], reason: str, idempotency_key: str
    ) -> Any:
        """Сохранить черновик → карточка версии; невалидный → 422 ``rating.ladder_invalid``."""
        return _card(
            self._client.put(
                f"{_LADDERS}/{ladder}/draft",
                idempotency_key=idempotency_key,
                json={"config": config, "reason": reason},
            )
        )

    def publish(self, ladder: str, *, version: int, reason: str, idempotency_key: str) -> Any:
        """Опубликовать версию-черновик (уходит ``LadderPublished``) → карточка версии."""
        return _card(
            self._client.post(
                f"{_LADDERS}/{ladder}/publish",
                idempotency_key=idempotency_key,
                json={"version": version, "reason": reason},
            )
        )

    def rollback(self, ladder: str, *, to_version: int, reason: str, idempotency_key: str) -> Any:
        """Откат: копия ранее опубликованной версии публикуется как новая."""
        return _card(
            self._client.post(
                f"{_LADDERS}/{ladder}/rollback",
                idempotency_key=idempotency_key,
                json={"to_version": to_version, "reason": reason},
            )
        )

    def create_season(
        self,
        ladder: str,
        *,
        starts_at: datetime | None,
        reason: str,
        idempotency_key: str,
    ) -> Any:
        """Создать сезон (``starts_at`` пусто — сразу или после текущего)."""
        body: dict[str, Any] = {"reason": reason}
        if starts_at is not None:
            body["starts_at"] = starts_at.isoformat()
        return self._client.post(
            f"{_LADDERS}/{ladder}/seasons", idempotency_key=idempotency_key, json=body
        )

    def close_season(self, season_id: UUID, *, reason: str, idempotency_key: str) -> Any:
        """Закрыть сезон досрочно: мягкий сброс, ``SeasonFinished``, следующий сезон."""
        return self._client.post(
            f"{_SEASONS}/{season_id}/close",
            idempotency_key=idempotency_key,
            json={"reason": reason},
        )

    def rebuild_leaderboard(self, ladder: str, *, reason: str, idempotency_key: str) -> Any:
        """Пересобрать лидерборд текущего сезона из Postgres → 202 ``{ladder, season_id}``."""
        return self._client.post(
            f"{_LADDERS}/{ladder}/leaderboard/rebuild",
            idempotency_key=idempotency_key,
            json={"reason": reason},
        )
