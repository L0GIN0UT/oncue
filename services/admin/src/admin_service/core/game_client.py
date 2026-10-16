"""Клиент admin-API game: конфиги режимов и наборы дня (docs/plans/stage-2.md §2.6).

Тонкая типизированная обёртка над ``ServiceClient``: пути, параметры и тела запросов в одном
месте, представления не собирают URL сами. Ответы возвращаются как есть (JSON) — их форму
проверяют представления и шаблоны, устойчивые к неожиданным данным.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import quote
from uuid import UUID

from admin_service.core.service_client import ServiceClient

GAME = "game"
_CONFIGS = "/admin/v1/mode-configs"
_DAILY = "/admin/v1/daily-sets"


def _key(mode_key: str) -> str:
    # Ключ уже проверен конвертером маршрута; quote — защита от ошибки маршрута, а не формат.
    return quote(mode_key, safe="")


class GameAdminClient:
    """Вызовы ``/admin/v1`` game от имени вошедшего модератора.

    Args:
        client: Клиент сервиса ``game`` (токен модератора, ``X-Request-Id``).
    """

    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    # ------------------------------------------------------------------ конфиги режимов
    def mode_configs(self, *, domain: str | None = None, status: str | None = None) -> Any:
        """Список режимов (``config.modes.view``)."""
        return self._client.get(_CONFIGS, params={"domain": domain, "status": status})

    def mode_config(self, mode_key: str) -> Any:
        """Режим: версии и черновик."""
        return self._client.get(f"{_CONFIGS}/{_key(mode_key)}")

    def version(self, mode_key: str, version: int) -> Any:
        """Опубликованная версия с телом."""
        return self._client.get(f"{_CONFIGS}/{_key(mode_key)}/versions/{int(version)}")

    def validate(self, body: dict[str, Any]) -> Any:
        """Проверка тела без сохранения → ``{ok, errors, warnings, pool}``."""
        return self._client.query(f"{_CONFIGS}/validate", json={"body": body})

    def save_draft(
        self,
        mode_key: str,
        *,
        body: dict[str, Any],
        base_revision: int | None,
        reason: str,
        idempotency_key: str,
    ) -> Any:
        """Сохранить черновик; ``base_revision`` устарела → 409 ``game.draft_conflict``."""
        return self._client.put(
            f"{_CONFIGS}/{_key(mode_key)}/draft",
            idempotency_key=idempotency_key,
            json={"body": body, "base_revision": base_revision, "reason": reason},
        )

    def publish(
        self, mode_key: str, *, draft_revision: int, reason: str, idempotency_key: str
    ) -> Any:
        """Опубликовать черновик ревизии ``draft_revision`` → новая версия."""
        return self._client.post(
            f"{_CONFIGS}/{_key(mode_key)}/publish",
            idempotency_key=idempotency_key,
            json={"draft_revision": draft_revision, "reason": reason},
        )

    def rollback(self, mode_key: str, *, to_version: int, reason: str, idempotency_key: str) -> Any:
        """Откат: копия версии ``to_version`` публикуется как новая версия."""
        return self._client.post(
            f"{_CONFIGS}/{_key(mode_key)}/rollback",
            idempotency_key=idempotency_key,
            json={"to_version": to_version, "reason": reason},
        )

    def archive(self, mode_key: str, *, reason: str, idempotency_key: str) -> Any:
        """Убрать режим из каталога."""
        return self._client.post(
            f"{_CONFIGS}/{_key(mode_key)}/archive",
            idempotency_key=idempotency_key,
            json={"reason": reason},
        )

    # ------------------------------------------------------------------ запросы ассетов
    def asset_requests(self, mode_key: str) -> Any:
        """Запросы ассетов режима (``config.modes.view``), статусы ``pending|sent``."""
        return self._client.get(f"{_CONFIGS}/{_key(mode_key)}/asset-requests")

    def request_assets(
        self, mode_key: str, *, draft_revision: int, reason: str, idempotency_key: str
    ) -> Any:
        """Запросить ассеты под загадку черновика (``config.modes.publish``, ADR 0025).

        409 ``game.assets_already_available`` — ассеты под параметры уже есть.
        """
        return self._client.post(
            f"{_CONFIGS}/{_key(mode_key)}/asset-requests",
            idempotency_key=idempotency_key,
            json={"draft_revision": draft_revision, "reason": reason},
        )

    # ------------------------------------------------------------------ наборы дня
    def daily_sets(self, mode_key: str, start: date, end: date) -> Any:
        """Наборы дня режима за ``start..end`` включительно (``game.daily.view``)."""
        return self._client.get(
            _DAILY,
            params={"mode_key": mode_key, "from": start.isoformat(), "to": end.isoformat()},
        )

    def replace_item(
        self,
        mode_key: str,
        day: date,
        index: int,
        *,
        media_item_id: UUID,
        reason: str,
        idempotency_key: str,
    ) -> Any:
        """Заменить единицу набора (только ``scheduled``)."""
        return self._client.put(
            f"{_DAILY}/{_key(mode_key)}/{day.isoformat()}/items/{int(index)}",
            idempotency_key=idempotency_key,
            json={"media_item_id": str(media_item_id), "reason": reason},
        )

    def regenerate(
        self,
        mode_key: str,
        day: date,
        *,
        reason: str,
        idempotency_key: str,
        subject_id: UUID | None = None,
    ) -> Any:
        """Перегенерировать набор (``scheduled`` / ``failed``).

        ``subject_id`` — новая тема периода (сетка недели); без него тему выбирает генератор.
        ``day`` — начало периода.
        """
        body: dict[str, Any] = {"reason": reason}
        if subject_id is not None:
            body["subject_id"] = str(subject_id)
        return self._client.post(
            f"{_DAILY}/{_key(mode_key)}/{day.isoformat()}/regenerate",
            idempotency_key=idempotency_key,
            json=body,
        )

    def projection_items(
        self,
        *,
        q: str | None = None,
        domain: str | None = None,
        playable_for_mode: str | None = None,
        limit: int | None = None,
    ) -> Any:
        """Кандидаты на замену из проекции game с признаком ``playable``."""
        return self._client.get(
            "/admin/v1/projection/items",
            params={
                "q": q,
                "domain": domain,
                "playable_for_mode": playable_for_mode,
                "limit": limit,
            },
        )
