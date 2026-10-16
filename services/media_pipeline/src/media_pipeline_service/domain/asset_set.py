"""Агрегат «набор ассетов загадки» и его жизненный цикл.

::

    pending ──start_attempt──▶ processing ──complete──▶ ready
       ▲                          │
       └──── fail(retryable) ─────┤──── fail(final) ──▶ failed
    любой ──disable──▶ disabled ──restore──▶ pending (новое поколение)
    ready|failed ──regenerate──▶ pending (новое поколение)

``generation`` — номер генерации: задача воркера несёт его в ``task_id``
(``<asset_set_id>:<generation>``), и устаревшая задача после перегенерации ничего не меняет.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID

from gv_core.domain import AggregateRoot, DomainEvent
from gv_core.errors import DomainError
from media_pipeline_service.domain.errors import (
    AssetBlockedError,
    AssetNotReadyError,
    LevelOutOfRangeError,
)
from media_pipeline_service.domain.profiles import AssetProfile
from media_pipeline_service.domain.sources import AssetScope


class AssetSetStatus(StrEnum):
    """Состояние набора."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class AssetLevel:
    """Объект одного уровня.

    Attributes:
        level: Номер уровня с 1.
        object_key: Случайный ключ объекта в бакете ассетов.
        mime: MIME объекта.
        size_bytes: Размер.
        duration_ms: Длительность (аудио).
        width: Ширина (изображения).
        height: Высота (изображения).
    """

    level: int
    object_key: str
    mime: str
    size_bytes: int
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, kw_only=True)
class AssetsReady(DomainEvent):
    """Набор готов (``media_pipeline.AssetsReady`` v1).

    ``aggregate_id`` — id единицы; ``version`` монотонна по единице (а не по набору):
    потребитель хранит последнюю версию на ``media_item_id`` и отбрасывает не большие.
    ``scope`` — владелец набора (ADR 0029): ``None`` — каталожный, сборник — свои загадки
    автора (выдаются только в сессиях этого сборника).
    """

    aggregate_type: ClassVar[str] = "asset_set"

    asset_set_id: UUID
    media_item_id: UUID
    clue_type: str
    profile_key: str
    params_hash: str
    levels_count: int
    version: int
    scope: AssetScope | None = None


@dataclass(frozen=True, kw_only=True)
class AssetsFailed(DomainEvent):
    """Генерация окончательно не удалась (``media_pipeline.AssetsFailed`` v1).

    ``version`` — из того же счётчика единицы, что и у ``AssetsReady``; ``scope`` — как там.
    """

    aggregate_type: ClassVar[str] = "asset_set"

    asset_set_id: UUID
    media_item_id: UUID
    clue_type: str
    profile_key: str
    error_code: str
    retryable: bool
    attempts: int
    version: int
    scope: AssetScope | None = None


class AssetSet(AggregateRoot[UUID]):
    """Набор ассетов одного профиля для одной единицы.

    ``version`` растёт на каждом изменении состояния; ``persisted_version`` — версия при
    загрузке, по ней репозиторий делает оптимистичную блокировку. В события идёт не она, а
    ``event_version`` — версия по единице (``MediaItemStateRepository.next_event_version``).
    ``scope`` — владелец набора (ADR 0029); ``None`` — каталожный набор. Набор уникален по
    ``(media_item_id, clue_type, profile_key, params_hash, scope)``.
    """

    def __init__(
        self,
        id: UUID,  # noqa: A002
        *,
        media_item_id: UUID,
        clue_type: str,
        profile_key: str,
        params: Mapping[str, Any],
        params_hash: str,
        levels_count: int,
        source_media_ids: Sequence[UUID],
        status: AssetSetStatus,
        generation: int,
        attempts: int,
        error_code: str | None,
        levels: Sequence[AssetLevel],
        version: int,
        created_at: datetime,
        updated_at: datetime,
        scope: AssetScope | None = None,
    ) -> None:
        super().__init__(id, version)
        self.media_item_id = media_item_id
        self.clue_type = clue_type
        self.profile_key = profile_key
        self.params = dict(params)
        self.params_hash = params_hash
        self.levels_count = levels_count
        self.source_media_ids: tuple[UUID, ...] = tuple(source_media_ids)
        self.status = status
        self.generation = generation
        self.attempts = attempts
        self.error_code = error_code
        self.levels: tuple[AssetLevel, ...] = tuple(levels)
        self.created_at = created_at
        self.updated_at = updated_at
        self.scope = scope
        self.persisted_version = version

    @property
    def source_media_id(self) -> UUID | None:
        """Первый (у большинства загадок — единственный) исходник набора."""
        return self.source_media_ids[0] if self.source_media_ids else None

    # ------------------------------------------------------------------ создание
    @classmethod
    def plan(
        cls,
        id: UUID,  # noqa: A002
        *,
        media_item_id: UUID,
        profile: AssetProfile,
        source_media_ids: Sequence[UUID],
        now: datetime,
        scope: AssetScope | None = None,
    ) -> AssetSet:
        """Новый набор в состоянии ``pending`` (первое поколение).

        Args:
            source_media_ids: Исходники в порядке, в котором их ждёт генератор
                (``sources_for`` генератора, ``frame_sequence`` — кадр на уровень).
            scope: Владелец набора (сборник); ``None`` — каталожный.
        """
        if not source_media_ids:
            raise DomainError("набору нужен хотя бы один исходник", code="asset_set.no_sources")
        aggregate = cls(
            id,
            media_item_id=media_item_id,
            clue_type=profile.clue_type,
            profile_key=profile.profile_key,
            params=profile.params_json(),
            params_hash=profile.params_hash,
            levels_count=profile.levels_count,
            source_media_ids=source_media_ids,
            status=AssetSetStatus.PENDING,
            generation=1,
            attempts=0,
            error_code=None,
            levels=(),
            version=1,
            created_at=now,
            updated_at=now,
            scope=scope,
        )
        aggregate.persisted_version = 0  # ещё не сохранён
        return aggregate

    # ------------------------------------------------------------------ чтение
    @property
    def is_new(self) -> bool:
        """Ещё не сохранён в репозитории."""
        return self.persisted_version == 0

    def level(self, number: int) -> AssetLevel:
        """Объект уровня для выдачи ссылки.

        Raises:
            AssetBlockedError: набор выключен (единица заблокирована).
            AssetNotReadyError: набор не готов.
            LevelOutOfRangeError: такого уровня нет.
        """
        if self.status is AssetSetStatus.DISABLED:
            raise AssetBlockedError("ассеты единицы выключены")
        if self.status is not AssetSetStatus.READY:
            raise AssetNotReadyError("набор ассетов ещё не готов")
        if not 1 <= number <= self.levels_count:
            raise LevelOutOfRangeError(
                f"уровень {number} вне диапазона 1..{self.levels_count}",
                details={"levels_count": self.levels_count},
            )
        return self.levels[number - 1]

    # ------------------------------------------------------------------ генерация
    def is_current(self, generation: int) -> bool:
        """Задача этого поколения актуальна и её можно выполнять."""
        return generation == self.generation and self.status in (
            AssetSetStatus.PENDING,
            AssetSetStatus.PROCESSING,
        )

    def start_attempt(self, generation: int, now: datetime) -> None:
        """Начать попытку генерации (повтор после падения воркера тоже допустим)."""
        self._require_current(generation)
        self.status = AssetSetStatus.PROCESSING
        self.attempts += 1
        self._touch(now)

    def complete(
        self,
        generation: int,
        levels: Sequence[AssetLevel],
        now: datetime,
        *,
        event_version: int,
    ) -> None:
        """Сохранить объекты уровней и опубликовать ``AssetsReady``.

        Args:
            event_version: Версия события по единице (монотонна по ``media_item_id``).
        """
        self._require_current(generation)
        if self.status is not AssetSetStatus.PROCESSING:
            raise DomainError("набор не в обработке", code="asset_set.invalid_transition")
        numbers = [lvl.level for lvl in levels]
        if numbers != list(range(1, self.levels_count + 1)):
            raise DomainError(
                f"ожидались уровни 1..{self.levels_count}, получены {numbers}",
                code="asset_set.levels_mismatch",
            )
        self.levels = tuple(levels)
        self.status = AssetSetStatus.READY
        self.error_code = None
        self._touch(now)
        self.record(
            AssetsReady(
                aggregate_id=str(self.media_item_id),
                asset_set_id=self.id,
                media_item_id=self.media_item_id,
                clue_type=self.clue_type,
                profile_key=self.profile_key,
                params_hash=self.params_hash,
                levels_count=self.levels_count,
                version=event_version,
                scope=self.scope,
            )
        )

    def fail(
        self,
        generation: int,
        error_code: str,
        *,
        retryable: bool,
        max_attempts: int,
        now: datetime,
        event_version: int,
    ) -> bool:
        """Зафиксировать неудачную попытку.

        Args:
            event_version: Версия ``AssetsFailed`` по единице; при повторяемой неудаче не
                используется (в счётчике остаётся пропуск — потребителю это не мешает).

        Returns:
            ``True`` — неудача окончательная (опубликовано ``AssetsFailed``);
            ``False`` — будет повтор, набор вернулся в ``pending``.
        """
        self._require_current(generation)
        self.error_code = error_code[:64]
        if retryable and self.attempts < max_attempts:
            self.status = AssetSetStatus.PENDING
            self._touch(now)
            return False
        self.status = AssetSetStatus.FAILED
        self._touch(now)
        self.record(
            AssetsFailed(
                aggregate_id=str(self.media_item_id),
                asset_set_id=self.id,
                media_item_id=self.media_item_id,
                clue_type=self.clue_type,
                profile_key=self.profile_key,
                error_code=self.error_code,
                retryable=retryable,
                attempts=max(self.attempts, 1),
                version=event_version,
                scope=self.scope,
            )
        )
        return True

    # ------------------------------------------------------------------ управление
    def regenerate(
        self, now: datetime, *, source_media_ids: Sequence[UUID] | None = None
    ) -> list[AssetLevel]:
        """Новое поколение генерации (админ или смена исходников).

        Args:
            source_media_ids: Новые исходники; ``None`` — прежние.

        Returns:
            Объекты прошлого поколения — их нужно удалить из хранилища после коммита.
        """
        if self.status is AssetSetStatus.DISABLED:
            raise AssetBlockedError("ассеты единицы выключены")
        return self._new_generation(now, source_media_ids)

    def disable(self, now: datetime) -> list[AssetLevel]:
        """Выключить набор (единица заблокирована). Возвращает объекты к удалению."""
        if self.status is AssetSetStatus.DISABLED:
            return []
        old, self.levels = list(self.levels), ()
        self.status = AssetSetStatus.DISABLED
        self._touch(now)
        return old

    def restore(self, now: datetime) -> None:
        """Включить после разблокировки: объекты удалены при блокировке, генерируем заново."""
        if self.status is not AssetSetStatus.DISABLED:
            return
        self._new_generation(now, None)

    def _new_generation(
        self, now: datetime, source_media_ids: Sequence[UUID] | None
    ) -> list[AssetLevel]:
        old, self.levels = list(self.levels), ()
        if source_media_ids:
            self.source_media_ids = tuple(source_media_ids)
        self.generation += 1
        self.attempts = 0
        self.error_code = None
        self.status = AssetSetStatus.PENDING
        self._touch(now)
        return old

    def _require_current(self, generation: int) -> None:
        if not self.is_current(generation):
            raise DomainError(
                "задача устарела: поколение сменилось или набор уже обработан",
                code="asset_set.stale_generation",
            )

    def _touch(self, now: datetime) -> None:
        self.version += 1
        self.updated_at = now
