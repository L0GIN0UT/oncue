"""Данные UGC-изображений на границе use-cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from media_pipeline_service.domain.ugc import UgcImage


@dataclass(frozen=True, slots=True)
class UgcImageView:
    """Ассет для API (без ключей объектов S3)."""

    asset_id: UUID
    owner_user_id: UUID
    purpose: str
    status: str
    reason_code: str | None
    width: int
    height: int
    size_bytes: int
    submitted_at: datetime
    decided_at: datetime | None
    moderator_id: UUID | None
    version: int

    @classmethod
    def of(cls, image: UgcImage) -> UgcImageView:
        """Из агрегата. У ``pending`` решений ещё нет - в контракте версия не меньше 1."""
        return cls(
            asset_id=image.id,
            owner_user_id=image.owner_user_id,
            purpose=image.purpose,
            status=image.status.value,
            reason_code=image.reason_code,
            width=image.width,
            height=image.height,
            size_bytes=image.size_bytes,
            submitted_at=image.submitted_at,
            decided_at=image.decided_at,
            moderator_id=image.moderator_id,
            version=max(1, image.version),
        )

    def to_json(self) -> dict[str, Any]:
        """JSON для хранения ответа идемпотентной операции."""
        return {
            "asset_id": str(self.asset_id),
            "owner_user_id": str(self.owner_user_id),
            "purpose": self.purpose,
            "status": self.status,
            "reason_code": self.reason_code,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "submitted_at": self.submitted_at.isoformat(),
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "moderator_id": str(self.moderator_id) if self.moderator_id else None,
            "version": self.version,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> UgcImageView:
        """Из сохранённого ответа."""
        return cls(
            asset_id=UUID(data["asset_id"]),
            owner_user_id=UUID(data["owner_user_id"]),
            purpose=data["purpose"],
            status=data["status"],
            reason_code=data["reason_code"],
            width=int(data["width"]),
            height=int(data["height"]),
            size_bytes=int(data["size_bytes"]),
            submitted_at=datetime.fromisoformat(data["submitted_at"]),
            decided_at=datetime.fromisoformat(data["decided_at"]) if data["decided_at"] else None,
            moderator_id=UUID(data["moderator_id"]) if data["moderator_id"] else None,
            version=int(data["version"]),
        )


@dataclass(frozen=True, slots=True)
class UgcPreview:
    """Ссылка для просмотра модератором."""

    url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class UgcPage:
    """Страница очереди."""

    items: list[UgcImageView]
    next_cursor: str | None
