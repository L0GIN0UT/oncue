"""Данные на входе и выходе use-cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from media_pipeline_service.domain.asset_set import AssetSet
from media_pipeline_service.domain.media_items import ItemStatus
from media_pipeline_service.domain.sources import SourceKind, SourceOrigin

GENERATE_TASK = "media.generate_asset_set"
"""Тип задачи RabbitMQ генерации набора; ``task_id = <asset_set_id>:<generation>``."""


@dataclass(frozen=True, slots=True)
class AssetSetView:
    """Набор ассетов для API."""

    asset_set_id: UUID
    media_item_id: UUID
    clue_type: str
    profile_key: str
    params_hash: str
    status: str
    levels_count: int
    generation: int
    error_code: str | None

    @classmethod
    def of(cls, aggregate: AssetSet) -> AssetSetView:
        """Из агрегата."""
        return cls(
            asset_set_id=aggregate.id,
            media_item_id=aggregate.media_item_id,
            clue_type=aggregate.clue_type,
            profile_key=aggregate.profile_key,
            params_hash=aggregate.params_hash,
            status=aggregate.status.value,
            levels_count=aggregate.levels_count,
            generation=aggregate.generation,
            error_code=aggregate.error_code,
        )

    def to_json(self) -> dict[str, Any]:
        """JSON-объект для хранения ответа идемпотентной мутации."""
        return {
            "asset_set_id": str(self.asset_set_id),
            "media_item_id": str(self.media_item_id),
            "clue_type": self.clue_type,
            "profile_key": self.profile_key,
            "params_hash": self.params_hash,
            "status": self.status,
            "levels_count": self.levels_count,
            "generation": self.generation,
            "error_code": self.error_code,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> AssetSetView:
        """Из сохранённого ответа (повтор запроса с тем же ``Idempotency-Key``)."""
        return cls(
            asset_set_id=UUID(data["asset_set_id"]),
            media_item_id=UUID(data["media_item_id"]),
            clue_type=data["clue_type"],
            profile_key=data["profile_key"],
            params_hash=data["params_hash"],
            status=data["status"],
            levels_count=int(data["levels_count"]),
            generation=int(data["generation"]),
            error_code=data["error_code"],
        )


@dataclass(frozen=True, slots=True)
class IssuedLink:
    """Выданная подписанная ссылка."""

    url: str
    expires_at: datetime
    mime: str
    level: int


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Происхождение загружаемого исходника (D6 плана этапа 3a).

    Attributes:
        origin: ``upload`` | ``seed`` | ``tmdb``; ``None`` — не указано (``upload``).
        source_url: Откуда взят файл.
        license_note: Основание использования.
    """

    origin: SourceOrigin | None = None
    source_url: str | None = None
    license_note: str | None = None

    @property
    def is_complete(self) -> bool:
        """Указаны все три поля (обязательно вне local/ci)."""
        return self.origin is not None and bool(self.source_url) and bool(self.license_note)


@dataclass(frozen=True, slots=True)
class UploadResult:
    """Результат загрузки исходника."""

    source_media_id: UUID
    media_item_id: UUID
    kind: SourceKind
    sha256: str
    size_bytes: int
    mime: str
    scheduled_asset_set_ids: tuple[UUID, ...]
    position: int = 1
    origin: SourceOrigin = SourceOrigin.UPLOAD

    def to_json(self) -> dict[str, Any]:
        """JSON-объект для хранения ответа идемпотентной мутации."""
        return {
            "source_media_id": str(self.source_media_id),
            "media_item_id": str(self.media_item_id),
            "kind": self.kind.value,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "mime": self.mime,
            "scheduled_asset_set_ids": [str(i) for i in self.scheduled_asset_set_ids],
            "position": self.position,
            "origin": self.origin.value,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> UploadResult:
        """Из сохранённого ответа (повтор запроса с тем же ``Idempotency-Key``)."""
        return cls(
            source_media_id=UUID(data["source_media_id"]),
            media_item_id=UUID(data["media_item_id"]),
            kind=SourceKind(data["kind"]),
            sha256=data["sha256"],
            size_bytes=int(data["size_bytes"]),
            mime=data["mime"],
            scheduled_asset_set_ids=tuple(UUID(i) for i in data["scheduled_asset_set_ids"]),
            position=int(data.get("position", 1)),
            origin=SourceOrigin(data.get("origin", SourceOrigin.UPLOAD.value)),
        )


@dataclass(frozen=True, slots=True)
class SourceView:
    """Исходник для admin-API (после смены порядка)."""

    source_media_id: UUID
    media_item_id: UUID
    kind: SourceKind
    origin: SourceOrigin
    position: int
    status: str
    scheduled_asset_set_ids: tuple[UUID, ...]

    def to_json(self) -> dict[str, Any]:
        """JSON-объект для хранения ответа идемпотентной мутации."""
        return {
            "source_media_id": str(self.source_media_id),
            "media_item_id": str(self.media_item_id),
            "kind": self.kind.value,
            "origin": self.origin.value,
            "position": self.position,
            "status": self.status,
            "scheduled_asset_set_ids": [str(i) for i in self.scheduled_asset_set_ids],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SourceView:
        """Из сохранённого ответа."""
        return cls(
            source_media_id=UUID(data["source_media_id"]),
            media_item_id=UUID(data["media_item_id"]),
            kind=SourceKind(data["kind"]),
            origin=SourceOrigin(data["origin"]),
            position=int(data["position"]),
            status=data["status"],
            scheduled_asset_set_ids=tuple(UUID(i) for i in data["scheduled_asset_set_ids"]),
        )


@dataclass(frozen=True, slots=True)
class CatalogSource:
    """Исходник из снимка единицы catalog (``source_media[]``)."""

    source_media_ref: UUID
    kind: SourceKind
    origin: SourceOrigin
    uri: str
    position: int = 1
    source_url: str | None = None
    license_note: str | None = None


@dataclass(frozen=True, slots=True)
class MediaItemSnapshot:
    """Полный снимок единицы из ``MediaItemCreated``/``MediaItemUpdated``."""

    media_item_id: UUID
    version: int
    domain: str
    kind: str
    status: ItemStatus
    sources: tuple[CatalogSource, ...]
