"""Данные на входе и выходе use-cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from media_pipeline_service.domain.asset_set import AssetSet
from media_pipeline_service.domain.media_items import ItemStatus
from media_pipeline_service.domain.sources import (
    AssetScope,
    ScopeKind,
    SourceKind,
    SourceMedia,
    SourceOrigin,
    SourceStatus,
)

GENERATE_TASK = "media.generate_asset_set"
PACKAGE_TASK = "media.package.process"
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
    # Владелец набора (ADR 0029); None — каталожный.
    scope: AssetScope | None = None

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
            scope=aggregate.scope,
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
            "scope": (
                {"kind": self.scope.kind.value, "id": str(self.scope.id)} if self.scope else None
            ),
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
            # Ответы, сохранённые до этапа 3b, поля не содержат.
            scope=(
                AssetScope(ScopeKind(scope["kind"]), UUID(scope["id"]))
                if (scope := data.get("scope"))
                else None
            ),
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
    """Происхождение загружаемого исходника (необязательно, ADR 0072).

    Attributes:
        origin: ``upload`` | ``seed`` | ``tmdb``; ``None`` — не указано (``upload``).
        source_url: Откуда взят файл.
        license_note: Основание использования.
    """

    origin: SourceOrigin | None = None
    source_url: str | None = None
    license_note: str | None = None


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
    source_missing: bool = False
    license_missing: bool = False

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
            "source_missing": self.source_missing,
            "license_missing": self.license_missing,
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
            source_missing=bool(data.get("source_missing", False)),
            license_missing=bool(data.get("license_missing", False)),
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
class SourceListView:
    """Исходник в списке админки (``GET /admin/v1/sources``)."""

    source_media_id: UUID
    kind: SourceKind
    origin: SourceOrigin
    position: int
    status: SourceStatus
    created_at: datetime
    media_item_id: UUID | None = None
    source_url: str | None = None
    license_note: str | None = None

    @property
    def source_missing(self) -> bool:
        """Источник не указан."""
        return not self.source_url

    @property
    def license_missing(self) -> bool:
        """Лицензия не указана."""
        return not self.license_note

    @classmethod
    def of(cls, source: SourceMedia) -> SourceListView:
        """Из сущности."""
        return cls(
            source_media_id=source.id,
            kind=source.kind,
            origin=source.origin,
            position=source.position,
            status=source.status,
            created_at=source.created_at,
            media_item_id=source.media_item_id,
            source_url=source.source_url,
            license_note=source.license_note,
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
class CatalogTexts:
    """Тексты загадок и метаданные единицы для текстового исходника (ADR 0024).

    Attributes:
        clue_texts: Одобренные тексты (``plot_masked``, ``tagline``, ``emoji``).
        directors: Режиссёры по порядку.
        cast: Актёры по порядку в титрах.
    """

    clue_texts: dict[str, str] = field(default_factory=dict)
    year: int | None = None
    genres: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    directors: tuple[str, ...] = ()
    cast: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MediaItemSnapshot:
    """Полный снимок единицы из ``MediaItemCreated``/``MediaItemUpdated``."""

    media_item_id: UUID
    version: int
    domain: str
    kind: str
    status: ItemStatus
    sources: tuple[CatalogSource, ...]
    texts: CatalogTexts = field(default_factory=CatalogTexts)
