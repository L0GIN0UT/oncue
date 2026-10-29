"""Клиент admin-API media-pipeline: наборы ассетов, профили, исходники (ADR 0013, 0025).

Тонкая обёртка над ``ServiceClient``: пути и тела запросов в одном месте. Ответы — как есть
(JSON), их форму проверяют представления и шаблоны.
"""

from __future__ import annotations

from typing import IO, Any
from uuid import UUID

from django.conf import settings

from admin_service.core.service_client import ServiceClient

MEDIA = "media_pipeline"
_SOURCES = "/admin/v1/sources"
UGC_PREVIEW_TYPE = "image/webp"
# Нормализованная копия исходника-изображения (ADR 0073): media хранит PNG.
SOURCE_PREVIEW_TYPE = "image/png"
UGC_PREVIEW_MAX_BYTES = 5 * 1024 * 1024
"""Потолок тела предпросмотра (лимиты назначений media - до 1,5 МБ)."""


class MediaAdminClient:
    """Вызовы ``/admin/v1`` media-pipeline от имени вошедшего модератора.

    Args:
        client: Клиент сервиса ``media_pipeline``.
    """

    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    def asset_sets(self, media_item_id: UUID) -> Any:
        """Наборы ассетов единицы (``admin.access``)."""
        return self._client.get(
            "/admin/v1/asset-sets", params={"media_item_id": str(media_item_id)}
        )

    def profiles(self, *, clue_type: str | None = None, params_hash: str | None = None) -> Any:
        """Профили ассетов и готовность их наборов (``admin.access``, этап 3a M6)."""
        return self._client.get(
            "/admin/v1/profiles", params={"clue_type": clue_type, "params_hash": params_hash}
        )

    def sources(self, media_item_id: UUID) -> Any:
        """Исходники единицы по виду и порядку (``admin.access``, этап 3b; без scoped)."""
        return self._client.get(_SOURCES, params={"media_item_id": str(media_item_id)})

    def regenerate(self, asset_set_id: UUID, *, reason: str, idempotency_key: str) -> Any:
        """Перегенерировать набор ассетов (``media.regenerate``)."""
        return self._client.post(
            f"/admin/v1/asset-sets/{asset_set_id}/regenerate",
            idempotency_key=idempotency_key,
            json={"reason": reason},
        )

    def upload_source(
        self,
        *,
        fields: dict[str, str],
        file_name: str,
        file: IO[bytes],
        content_type: str,
        idempotency_key: str,
    ) -> Any:
        """Загрузить исходник (multipart, ``media.upload_source``).

        ``fields`` — поля формы (``media_item_id``, ``kind``, ``reason``, ``position`` и
        необязательные ``origin``/``source_url``/``license_note``) уже строками.
        """
        return self._client.post(
            _SOURCES,
            idempotency_key=idempotency_key,
            data=fields,
            files={"file": (file_name, file, content_type)},
        )

    def set_source_position(
        self, source_media_id: UUID, *, position: int, reason: str, idempotency_key: str
    ) -> Any:
        """Сменить порядок исходника среди своего вида (кадры ``frame_sequence``)."""
        return self._client.put(
            f"{_SOURCES}/{source_media_id}/position",
            idempotency_key=idempotency_key,
            json={"position": position, "reason": reason},
        )

    # ------------------------------------------------------------------ UGC-изображения (5b)

    def ugc_images(
        self,
        *,
        status: str = "pending",
        purpose: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Any:
        """Очередь UGC-изображений → ``{items, next_cursor}`` (``media.ugc.moderate``)."""
        return self._client.get(
            "/admin/v1/ugc-images",
            params={"status": status, "purpose": purpose, "cursor": cursor, "limit": limit},
        )

    def ugc_image(self, asset_id: UUID) -> Any:
        """Карточка UGC-изображения по id → поля элемента очереди (``media.ugc.moderate``)."""
        return self._client.get(f"/admin/v1/ugc-images/{asset_id}")

    def ugc_settings(self) -> Any:
        """Настройки модерации → ``{purposes, reasons[{code, title}]}``."""
        return self._client.get("/admin/v1/ugc-images/settings")

    def source_preview(self, source_media_id: UUID) -> bytes:
        """Байты PNG каталожного исходника-изображения (обложка, постер, кадр)."""
        return self._client.get_bytes(
            f"/admin/v1/sources/{source_media_id}/preview",
            accept=SOURCE_PREVIEW_TYPE,
            max_bytes=int(settings.GV_MAX_UPLOAD_MB) * 1024 * 1024,
        )

    def ugc_preview(self, asset_id: UUID) -> bytes:
        """Байты WebP ``pending``-изображения (ADR 0061); ссылок и адресов S3 нет."""
        return self._client.get_bytes(
            f"/admin/v1/ugc-images/{asset_id}/preview",
            accept=UGC_PREVIEW_TYPE,
            max_bytes=UGC_PREVIEW_MAX_BYTES,
        )

    def ugc_approve(self, asset_id: UUID, *, reason: str, idempotency_key: str) -> Any:
        """Одобрить изображение."""
        return self._client.post(
            f"/admin/v1/ugc-images/{asset_id}/approve",
            idempotency_key=idempotency_key,
            json={"reason": reason},
        )

    def ugc_reject(
        self, asset_id: UUID, *, reason_code: str, reason: str, idempotency_key: str
    ) -> Any:
        """Отклонить (из ``pending``) или снять одобренное (из ``approved``)."""
        return self._client.post(
            f"/admin/v1/ugc-images/{asset_id}/reject",
            idempotency_key=idempotency_key,
            json={"reason_code": reason_code, "reason": reason},
        )
