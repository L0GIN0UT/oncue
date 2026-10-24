"""Клиент admin-API social: очередь модерации, игрок, санкции, конфиг (этап 5c, поток F4).

Тонкая обёртка над ``ServiceClient``: пути и тела запросов в одном месте, ответы - как есть
(JSON). Права проверяет social: ``admin.access`` + ``social.moderate`` (очередь, игрок),
``social.sanctions.manage`` (санкции; ``account`` - ещё ``social.sanctions.account``),
``social.config.manage|publish`` (конфиг). Запросы - по ``contracts/openapi/social.json``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from admin_service.core.service_client import ServiceClient

SOCIAL = "social"
_CASES = "/admin/v1/cases"
_CONFIG = "/admin/v1/config"


class SocialAdminClient:
    """Вызовы ``/admin/v1`` social от имени вошедшего администратора.

    Args:
        client: Клиент сервиса ``social`` (токен администратора, ``X-Request-Id``).
    """

    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    # ------------------------------------------------------------------ очередь
    def cases(
        self,
        *,
        status: str | None = None,
        target_kind: str | None = None,
        mine: bool = False,
        cursor: str | None = None,
        limit: int = 30,
    ) -> Any:
        """Очередь (старые первыми) → ``{items, next_cursor}``."""
        return self._client.get(
            _CASES,
            params={
                "status": status,
                "target_kind": target_kind,
                "mine": "true" if mine else None,
                "cursor": cursor,
                "limit": limit,
            },
        )

    def case(self, case_id: UUID) -> Any:
        """Карточка кейса (жалобы с текстом, фрагмент содержимого)."""
        return self._client.get(f"{_CASES}/{case_id}")

    def claim(self, case_id: UUID, *, version: int, idempotency_key: str) -> Any:
        """Взять в работу."""
        return self._client.post(
            f"{_CASES}/{case_id}/claim", idempotency_key=idempotency_key, json={"version": version}
        )

    def escalate(self, case_id: UUID, *, version: int, reason: str, idempotency_key: str) -> Any:
        """Эскалировать (решает только старший)."""
        return self._client.post(
            f"{_CASES}/{case_id}/escalate",
            idempotency_key=idempotency_key,
            json={"version": version, "reason": reason},
        )

    def resolve(
        self,
        case_id: UUID,
        *,
        version: int,
        decision: str,
        reason: str,
        idempotency_key: str,
        sanction: dict[str, Any] | None = None,
    ) -> Any:
        """Решить: ``dismissed | content_removed | sanctioned`` (санкция - в теле)."""
        body: dict[str, Any] = {"version": version, "decision": decision, "reason": reason}
        if sanction is not None:
            body["sanction"] = sanction
        return self._client.post(
            f"{_CASES}/{case_id}/resolve", idempotency_key=idempotency_key, json=body
        )

    # ------------------------------------------------------------------ игрок и санкции
    def user_moderation(self, user_id: UUID) -> Any:
        """Карточка игрока: счётчики порядочности, санкции, кейсы."""
        return self._client.get(f"/admin/v1/users/{user_id}/moderation")

    def grant_sanction(
        self,
        user_id: UUID,
        *,
        kind: str,
        duration_s: int | None,
        reason: str,
        idempotency_key: str,
    ) -> Any:
        """Выдать санкцию (``duration_s = None`` - бессрочно)."""
        return self._client.post(
            f"/admin/v1/users/{user_id}/sanctions",
            idempotency_key=idempotency_key,
            json={"kind": kind, "duration_s": duration_s, "reason": reason},
        )

    def revoke_sanction(self, sanction_id: UUID, *, reason: str, idempotency_key: str) -> Any:
        """Снять санкцию вручную (история сохраняется)."""
        return self._client.post(
            f"/admin/v1/sanctions/{sanction_id}/revoke",
            idempotency_key=idempotency_key,
            json={"reason": reason},
        )

    # ------------------------------------------------------------------ конфиг
    def versions(self) -> Any:
        """Версии конфига (новые первыми) → ``{items: [ConfigVersionCard]}``."""
        return self._client.get(f"{_CONFIG}/versions")

    def version(self, version: int) -> Any:
        """Версия с телом → ``{card, config}``."""
        return self._client.get(f"{_CONFIG}/versions/{int(version)}")

    def save_draft(self, *, config: dict[str, Any], reason: str, idempotency_key: str) -> Any:
        """Сохранить черновик (он один) → ``{card, config}``; невалидный → 422."""
        return self._client.put(
            f"{_CONFIG}/draft",
            idempotency_key=idempotency_key,
            json={"config": config, "reason": reason},
        )

    def publish(self, *, version: int, body_sha256: str, reason: str, idempotency_key: str) -> Any:
        """Опубликовать черновик (привязка к хешу тела) → ``{card, config}``."""
        return self._client.post(
            f"{_CONFIG}/draft/publish",
            idempotency_key=idempotency_key,
            json={"version": version, "body_sha256": body_sha256, "reason": reason},
        )

    def rollback(self, version: int, *, reason: str, idempotency_key: str) -> Any:
        """Копия старой версии становится черновиком → ``{card, config}``."""
        return self._client.post(
            f"{_CONFIG}/versions/{int(version)}/rollback",
            idempotency_key=idempotency_key,
            json={"reason": reason},
        )
