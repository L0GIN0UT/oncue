"""Конфиги режимов: неизменяемые опубликованные версии и один черновик на ключ (D8).

Агрегат ``ModeLine`` — все конфиги одного ``mode_key``. Опубликованная версия не меняется
никогда (кроме отметки «архив»): публикация, откат и повтор публикации создают новую
версию. Черновик сохраняется и невалидным (итог проверки хранится рядом), а публикуется
только валидный и только той ревизии, которую видел автор (оптимистичная блокировка).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from game_service.domain.errors import (
    DraftConflictError,
    DraftNotFoundError,
    ModeNotPublishedError,
    ModeVersionNotFoundError,
)
from game_service.domain.events import ModeConfigArchived, ModeConfigPublished
from gv_core.domain import AggregateRoot
from gv_game_kernel import ConfigError, params_hash

# Any: тело конфига — JSON-объект автора; структуру проверяет ядро движка.
type ConfigBody = dict[str, Any]


class ModeStatus(StrEnum):
    """Статус режима целиком (строка списка в админке)."""

    DRAFT = "draft"  # ни разу не опубликован
    PUBLISHED = "published"
    ARCHIVED = "archived"


class VersionStatus(StrEnum):
    """Статус опубликованной версии."""

    PUBLISHED = "published"
    ARCHIVED = "archived"


def body_hash(body: Mapping[str, Any]) -> str:
    """sha256 канонического JSON тела (тот же алгоритм, что ``params_hash``)."""
    return params_hash(body)


def domain_of(mode_key: str) -> str:
    """Домен режима — первый сегмент ключа (``music.daily.classic`` → ``music``)."""
    return mode_key.split(".", 1)[0]


@dataclass(frozen=True, slots=True)
class ConfigVersion:
    """Опубликованная версия конфига.

    Attributes:
        key: Ключ режима.
        version: Номер версии (с 1, монотонно по ключу).
        domain: Домен режима.
        status: ``published`` или ``archived``.
        body: Тело конфига.
        body_hash: sha256 канонического JSON тела.
        actor_id: Кто опубликовал (``None`` — bootstrap сидов).
        reason: Причина (аудит).
        created_at: Момент публикации.
        rolled_back_from: Версия, копией которой является эта (откат).
    """

    key: str
    version: int
    domain: str
    status: VersionStatus
    body: ConfigBody
    body_hash: str
    actor_id: UUID | None
    reason: str | None
    created_at: datetime
    rolled_back_from: int | None = None


@dataclass(frozen=True, slots=True)
class Draft:
    """Черновик конфига.

    Attributes:
        body: Тело (может быть невалидным).
        revision: Ревизия (с 1); растёт при каждом сохранении.
        validation: Итог последней проверки (``{ok, errors, warnings, pool}``).
        updated_by: Автор последней правки.
        updated_at: Время последней правки.
    """

    body: ConfigBody
    revision: int
    validation: dict[str, Any]
    updated_by: UUID | None
    updated_at: datetime

    @property
    def is_valid(self) -> bool:
        """Черновик прошёл проверку при сохранении."""
        return bool(self.validation.get("ok"))


@dataclass(eq=False)
class ModeLine(AggregateRoot[str]):
    """Конфиги одного режима.

    Attributes:
        latest: Последняя опубликованная версия (``None`` — режим не публиковался).
        draft: Черновик (``None`` — нет).
        new_versions: Версии, созданные в этой транзакции (репозиторий их вставит).
        archived_version: Версия, помеченная архивной в этой транзакции.
        draft_dirty: Черновик изменён или удалён в этой транзакции.
    """

    key: str
    latest: ConfigVersion | None = None
    draft: Draft | None = None
    new_versions: list[ConfigVersion] = field(default_factory=list)
    archived_version: int | None = None
    draft_dirty: bool = False

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self, self.key)

    # ------------------------------------------------------------------ состояние
    @property
    def domain(self) -> str:
        """Домен режима."""
        return domain_of(self.key)

    @property
    def status(self) -> ModeStatus:
        """Статус режима по последней версии."""
        if self.latest is None:
            return ModeStatus.DRAFT
        if self.latest.status is VersionStatus.ARCHIVED:
            return ModeStatus.ARCHIVED
        return ModeStatus.PUBLISHED

    @property
    def published_version(self) -> int | None:
        """Номер последней опубликованной версии (в том числе архивной)."""
        return self.latest.version if self.latest else None

    # ------------------------------------------------------------------ черновик
    def save_draft(
        self,
        body: ConfigBody,
        *,
        base_revision: int | None,
        validation: dict[str, Any],
        actor_id: UUID | None,
        now: datetime,
    ) -> Draft:
        """Сохранить черновик (и невалидный).

        Raises:
            DraftConflictError: ``base_revision`` не совпадает с текущей ревизией черновика.
        """
        current = self.draft.revision if self.draft else None
        if base_revision != current:
            raise DraftConflictError(
                "черновик изменился с момента загрузки",
                details={"revision": current},
            )
        self.draft = Draft(
            body=body,
            revision=(current or 0) + 1,
            validation=validation,
            updated_by=actor_id,
            updated_at=now,
        )
        self.draft_dirty = True
        return self.draft

    def require_draft(self, revision: int) -> Draft:
        """Черновик ровно этой ревизии.

        Raises:
            DraftNotFoundError: черновика нет.
            DraftConflictError: ревизия устарела.
        """
        if self.draft is None:
            raise DraftNotFoundError("черновика нет")
        if self.draft.revision != revision:
            raise DraftConflictError(
                "черновик изменился с момента проверки",
                details={"revision": self.draft.revision},
            )
        return self.draft

    # ------------------------------------------------------------------ публикация
    def publish(
        self,
        body: ConfigBody,
        *,
        actor_id: UUID | None,
        reason: str | None,
        now: datetime,
        rolled_back_from: int | None = None,
    ) -> ConfigVersion:
        """Опубликовать тело как новую версию (валидность проверяет application).

        Черновик после обычной публикации удаляется: он стал опубликованной версией.
        """
        version = ConfigVersion(
            key=self.key,
            version=(self.published_version or 0) + 1,
            domain=self.domain,
            status=VersionStatus.PUBLISHED,
            body=body,
            body_hash=body_hash(body),
            actor_id=actor_id,
            reason=reason,
            created_at=now,
            rolled_back_from=rolled_back_from,
        )
        self.latest = version
        self.new_versions.append(version)
        if rolled_back_from is None and self.draft is not None:
            self.draft = None
            self.draft_dirty = True
        self.record(
            ModeConfigPublished(
                aggregate_id=self.key,
                mode_key=self.key,
                version=version.version,
                domain=self.domain,
                body_hash=version.body_hash,
                actor_id=actor_id,
                published_at=now,
                rolled_back_from=rolled_back_from,
            )
        )
        return version

    def publish_draft(
        self, revision: int, *, actor_id: UUID | None, reason: str, now: datetime
    ) -> ConfigVersion:
        """Опубликовать черновик ревизии ``revision``.

        Raises:
            DraftNotFoundError, DraftConflictError: см. ``require_draft``.
            ConfigError: черновик невалиден (ошибки — в ``details.errors``).
        """
        draft = self.require_draft(revision)
        if not draft.is_valid:
            raise ConfigError(details={"errors": list(draft.validation.get("errors", []))})
        return self.publish(draft.body, actor_id=actor_id, reason=reason, now=now)

    def rollback(
        self, source: ConfigVersion, *, actor_id: UUID | None, reason: str, now: datetime
    ) -> ConfigVersion:
        """Откат: копия версии ``source`` публикуется как новая версия.

        Raises:
            ModeVersionNotFoundError: версия чужого режима.
        """
        if source.key != self.key:
            raise ModeVersionNotFoundError("версия другого режима")
        return self.publish(
            source.body,
            actor_id=actor_id,
            reason=reason,
            now=now,
            rolled_back_from=source.version,
        )

    def archive(self, *, actor_id: UUID | None, now: datetime) -> ConfigVersion:
        """Убрать режим из каталога: последняя версия становится архивной (причина — в аудите).

        Raises:
            ModeNotPublishedError: режим не опубликован или уже в архиве.
        """
        if self.latest is None or self.latest.status is not VersionStatus.PUBLISHED:
            raise ModeNotPublishedError("режим не опубликован")
        self.latest = replace(self.latest, status=VersionStatus.ARCHIVED)
        self.archived_version = self.latest.version
        self.record(
            ModeConfigArchived(
                aggregate_id=self.key,
                mode_key=self.key,
                version=self.latest.version,
                actor_id=actor_id,
                archived_at=now,
            )
        )
        return self.latest
