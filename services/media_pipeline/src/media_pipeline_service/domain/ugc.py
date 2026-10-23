"""UGC-изображения (аватарки, ADR 0048): назначения из конфига и жизненный цикл ассета.

::

    pending ──approve──▶ approved ──reject (снятие)──▶ rejected
       │
       ├──reject──▶ rejected
       └──supersede (новая заявка владельца)──▶ rejected(superseded), без события

``version`` - число решений модератора: 0 у ``pending``, 1 после первого решения, +1 при
снятии одобренного. Именно она идёт в ``AssetModerated``. Тихая отмена прежней заявки
(``supersede``) событие не публикует и версию не меняет.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from gv_core.domain import AggregateRoot, DomainEvent
from media_pipeline_service.domain.errors import (
    UgcNotPendingError,
    UgcPurposeUnknownError,
    UgcReasonUnknownError,
)

SUPERSEDED_REASON = "superseded"
"""Код прежней заявки, отменённой новой: служебный, в списке причин конфига его нет."""
REPLACED_REASON = "replaced"
"""Код одобренной аватарки, замещённой новой одобренной: служебный, события нет."""

PENDING_PREFIX = "p/"
PUBLIC_PREFIX = "u/"
_KEY_RE = re.compile(r"^(?P<prefix>[pu])/[a-f0-9]{32}\.webp$")


def pending_key(token_hex: str) -> str:
    """Ключ непроверенного объекта; ``token_hex`` - только из CSPRNG."""
    return f"{PENDING_PREFIX}{token_hex}.webp"


def public_key(token_hex: str) -> str:
    """Ключ одобренного объекта в публичном префиксе."""
    return f"{PUBLIC_PREFIX}{token_hex}.webp"


def is_object_key(key: str) -> bool:
    """Ключ имеет форму ``p/<32 hex>.webp`` или ``u/<32 hex>.webp``."""
    return _KEY_RE.match(key) is not None


class UgcStatus(StrEnum):
    """Статус ассета."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class UgcPurpose:
    """Назначение изображения из ``ugc_purposes.yaml``."""

    key: str
    max_bytes: int
    input_formats: tuple[str, ...]
    min_side_px: int
    max_side_px: int
    output_side_px: int
    allowed_clients: frozenset[str]


@dataclass(frozen=True, slots=True)
class UgcReason:
    """Код причины отклонения и его заголовок для модератора."""

    code: str
    title: str


@dataclass(frozen=True, slots=True)
class UgcCatalog:
    """Назначения и причины отклонения."""

    purposes: Mapping[str, UgcPurpose]
    reasons: Sequence[UgcReason]

    def purpose(self, key: str) -> UgcPurpose:
        """Назначение по ключу.

        Raises:
            UgcPurposeUnknownError: нет в конфиге (422 ``ugc.purpose_unknown``).
        """
        found = self.purposes.get(key)
        if found is None:
            raise UgcPurposeUnknownError("назначение изображения не поддерживается")
        return found

    def check_reason(self, code: str) -> None:
        """Код причины должен быть из конфига.

        Raises:
            UgcReasonUnknownError: иначе (422 ``ugc.reason_unknown``).
        """
        if code not in {r.code for r in self.reasons}:
            raise UgcReasonUnknownError("неизвестный код причины отклонения")


@dataclass(frozen=True, kw_only=True)
class AssetModerated(DomainEvent):
    """Решение по UGC-изображению (``media_pipeline.AssetModerated`` v1).

    ``aggregate_id`` - id ассета. Модератор и свободный текст в событие не попадают.
    """

    aggregate_type: ClassVar[str] = "ugc_image"

    asset_id: UUID
    owner_user_id: UUID
    purpose: str
    status: str
    reason_code: str | None
    public_key: str | None
    decided_at: datetime
    version: int


class UgcImage(AggregateRoot[UUID]):
    """Загруженное изображение с очередью на модерацию."""

    def __init__(
        self,
        id: UUID,  # noqa: A002
        *,
        owner_user_id: UUID,
        purpose: str,
        status: UgcStatus,
        reason_code: str | None,
        pending_key: str | None,
        public_key: str | None,
        width: int,
        height: int,
        size_bytes: int,
        sha256: str,
        submitted_at: datetime,
        decided_at: datetime | None,
        moderator_id: UUID | None,
        version: int,
    ) -> None:
        super().__init__(id, version)
        self.owner_user_id = owner_user_id
        self.purpose = purpose
        self.status = status
        self.reason_code = reason_code
        self.pending_key = pending_key
        self.public_key = public_key
        self.width = width
        self.height = height
        self.size_bytes = size_bytes
        self.sha256 = sha256
        self.submitted_at = submitted_at
        self.decided_at = decided_at
        self.moderator_id = moderator_id

    @classmethod
    def submit(
        cls,
        id: UUID,  # noqa: A002
        *,
        owner_user_id: UUID,
        purpose: str,
        pending_key: str,
        width: int,
        height: int,
        size_bytes: int,
        sha256: str,
        now: datetime,
    ) -> UgcImage:
        """Новая заявка в статусе ``pending``."""
        return cls(
            id,
            owner_user_id=owner_user_id,
            purpose=purpose,
            status=UgcStatus.PENDING,
            reason_code=None,
            pending_key=pending_key,
            public_key=None,
            width=width,
            height=height,
            size_bytes=size_bytes,
            sha256=sha256,
            submitted_at=now,
            decided_at=None,
            moderator_id=None,
            version=0,
        )

    def approve(self, *, moderator_id: UUID, new_public_key: str, now: datetime) -> str:
        """Одобрить: ``pending → approved``.

        Returns:
            Ключ ``p/``-объекта, который нужно удалить после коммита.

        Raises:
            UgcNotPendingError: статус не ``pending`` (409).
        """
        old_key = self._require_pending()
        self.status = UgcStatus.APPROVED
        self.reason_code = None
        self.pending_key = None
        self.public_key = new_public_key
        self._decide(moderator_id, now)
        self._emit(now)
        return old_key

    def reject(self, *, moderator_id: UUID, reason_code: str, now: datetime) -> str:
        """Отклонить ``pending`` или снять ``approved`` (версия растёт).

        Returns:
            Ключ объекта, который нужно удалить после коммита.

        Raises:
            UgcNotPendingError: уже ``rejected`` (409).
        """
        if self.status is UgcStatus.REJECTED:
            raise UgcNotPendingError("изображение уже отклонено")
        key = self.pending_key if self.status is UgcStatus.PENDING else self.public_key
        if key is None:  # инвариант схемы: у pending/approved ключ есть
            raise UgcNotPendingError("у изображения нет объекта")
        self.status = UgcStatus.REJECTED
        self.reason_code = reason_code
        self.pending_key = None
        self.public_key = None
        self._decide(moderator_id, now)
        self._emit(now)
        return key

    def supersede(self, now: datetime) -> str:
        """Тихо снять прежнюю ``pending`` новой заявкой владельца (события нет).

        Returns:
            Ключ ``p/``-объекта, который нужно удалить после коммита.
        """
        old_key = self._require_pending()
        self.status = UgcStatus.REJECTED
        self.reason_code = SUPERSEDED_REASON
        self.pending_key = None
        self.decided_at = now
        return old_key

    def replace(self, now: datetime) -> str:
        """Тихо заместить одобренное изображение новым одобренным того же владельца.

        Версия растёт (решение пересмотрено), события нет: потребитель и так получает
        ``approved`` нового ассета, а пользователю «отклонено» слать нельзя.

        Returns:
            Ключ публичного ``u/``-объекта, который нужно удалить.

        Raises:
            UgcNotPendingError: статус не ``approved``.
        """
        if self.status is not UgcStatus.APPROVED or self.public_key is None:
            raise UgcNotPendingError("изображение не одобрено")
        old_key = self.public_key
        self.status = UgcStatus.REJECTED
        self.reason_code = REPLACED_REASON
        self.public_key = None
        self.decided_at = now
        self.version += 1
        return old_key

    def _require_pending(self) -> str:
        if self.status is not UgcStatus.PENDING or self.pending_key is None:
            raise UgcNotPendingError("изображение не ожидает решения")
        return self.pending_key

    def _decide(self, moderator_id: UUID, now: datetime) -> None:
        self.moderator_id = moderator_id
        self.decided_at = now
        self.version += 1

    def _emit(self, now: datetime) -> None:
        self.record(
            AssetModerated(
                aggregate_id=str(self.id),
                asset_id=self.id,
                owner_user_id=self.owner_user_id,
                purpose=self.purpose,
                status=self.status.value,
                reason_code=self.reason_code,
                public_key=self.public_key,
                decided_at=now,
                version=self.version,
            )
        )
