"""Линия версий ladder-а: черновик → публикация → откат (ADR 0037)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from gv_core.domain import AggregateRoot
from rating_service.domain.errors import (
    LadderInvalidError,
    VersionNotDraftError,
    VersionNotFoundError,
    VersionNotPublishedError,
)
from rating_service.domain.events import LadderPublished
from rating_service.domain.ladder import LadderConfig


class VersionStatus(StrEnum):
    """Состояние версии конфига."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


@dataclass(slots=True)
class LadderVersion:
    """Версия конфига ladder-а."""

    version: int
    status: VersionStatus
    config: LadderConfig
    reason: str
    created_by: UUID
    created_at: datetime
    published_at: datetime | None = None


class LadderLine(AggregateRoot[str]):
    """Ladder и все версии его конфига."""

    def __init__(
        self, key: str, domain: str, created_at: datetime, versions: list[LadderVersion]
    ) -> None:
        super().__init__(key)
        self.key = key
        self.domain = domain
        self.created_at = created_at
        self.versions = versions

    # ------------------------------------------------------------------ создание
    @classmethod
    def create(
        cls, config: LadderConfig, *, actor_id: UUID, reason: str, now: datetime
    ) -> LadderLine:
        """Новый ladder сразу с опубликованной версией 1 (сиды, ``bootstrap``)."""
        line = cls(config.key, config.domain, now, [])
        line.versions.append(LadderVersion(1, VersionStatus.DRAFT, config, reason, actor_id, now))
        line.publish(1, now=now)
        return line

    # ------------------------------------------------------------------ чтение
    @property
    def draft(self) -> LadderVersion | None:
        """Текущий черновик."""
        return next((v for v in self.versions if v.status is VersionStatus.DRAFT), None)

    @property
    def published(self) -> LadderVersion | None:
        """Действующая версия."""
        return next((v for v in self.versions if v.status is VersionStatus.PUBLISHED), None)

    def get(self, version: int) -> LadderVersion:
        """Версия по номеру.

        Raises:
            VersionNotFoundError: нет такой версии.
        """
        found = next((v for v in self.versions if v.version == version), None)
        if found is None:
            raise VersionNotFoundError()
        return found

    def _next_number(self) -> int:
        return max((v.version for v in self.versions), default=0) + 1

    def _check_identity(self, config: LadderConfig) -> None:
        errors: list[dict[str, str]] = []
        if config.key != self.key:
            errors.append(
                {"path": "/key", "message": "ключ в теле должен совпадать с ключом ladder-а"}
            )
        if config.domain != self.domain:
            errors.append({"path": "/domain", "message": "домен ladder-а менять нельзя"})
        if errors:
            raise LadderInvalidError(errors)

    # ------------------------------------------------------------------ мутации
    def save_draft(
        self, config: LadderConfig, *, actor_id: UUID, reason: str, now: datetime
    ) -> LadderVersion:
        """Сохранить черновик: заменяет текущий или создаёт новую версию.

        Raises:
            LadderInvalidError: ключ или домен не совпадают с ladder-ом.
        """
        self._check_identity(config)
        draft = self.draft
        if draft is not None:
            draft.config, draft.reason, draft.created_by, draft.created_at = (
                config,
                reason,
                actor_id,
                now,
            )
            return draft
        draft = LadderVersion(
            self._next_number(), VersionStatus.DRAFT, config, reason, actor_id, now
        )
        self.versions.append(draft)
        return draft

    def publish(self, version: int, *, now: datetime) -> LadderVersion:
        """Сделать черновик ``version`` действующим (прежняя действующая → архив).

        Raises:
            VersionNotFoundError: нет версии.
            VersionNotDraftError: версия не черновик.
        """
        target = self.get(version)
        if target.status is not VersionStatus.DRAFT:
            raise VersionNotDraftError("публиковать можно только черновик")
        self._activate(target, now)
        return target

    def rollback(
        self, to_version: int, *, actor_id: UUID, reason: str, now: datetime
    ) -> LadderVersion:
        """Опубликовать копию ранее опубликованной версии новой версией.

        Raises:
            VersionNotFoundError: нет версии.
            VersionNotPublishedError: версия никогда не публиковалась.
        """
        source = self.get(to_version)
        if source.published_at is None:
            raise VersionNotPublishedError("откатываться можно только к опубликованной версии")
        copy = LadderVersion(
            self._next_number(), VersionStatus.DRAFT, source.config, reason, actor_id, now
        )
        self.versions.append(copy)
        self._activate(copy, now)
        return copy

    def _activate(self, target: LadderVersion, now: datetime) -> None:
        current = self.published
        if current is not None:
            current.status = VersionStatus.ARCHIVED
        target.status = VersionStatus.PUBLISHED
        target.published_at = now
        mm = target.config.matchmaking
        self.record(
            LadderPublished(
                aggregate_id=self.key,
                ladder=self.key,
                version=target.version,
                domain=self.domain,
                formats=target.config.formats,
                matchmaking={
                    "window": {
                        "base": mm.window.base,
                        "step": mm.window.step,
                        "step_every_s": mm.window.step_every_s,
                        "max": mm.window.max,
                    },
                    "slow_hint_after_s": mm.slow_hint_after_s,
                    "interval_s": mm.interval_s,
                },
                published_at=now,
            )
        )
