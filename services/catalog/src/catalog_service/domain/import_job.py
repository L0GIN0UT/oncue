"""Агрегат ``ImportJob`` — задание импорта метаданных из внешнего источника или файла."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from gv_core.domain import AggregateRoot
from gv_core.errors import ConflictError

MAX_ERROR_LEN = 500


class ImportSource(StrEnum):
    """Источник импорта (контракт admin-API ``POST /admin/v1/imports``)."""

    TMDB = "tmdb"
    FILE = "file"
    MUSICBRAINZ = "musicbrainz"
    SPOTIFY = "spotify"
    YANDEX_MUSIC = "yandex_music"


class ImportStatus(StrEnum):
    """Состояние задания."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ItemOutcome(StrEnum):
    """Итог импорта одной сущности."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    FAILED = "failed"


@dataclass(slots=True)
class ImportCounters:
    """Счётчики задания."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        """Для хранения в JSONB и ответа API."""
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "failed": self.failed,
        }


class ImportJob(AggregateRoot[uuid.UUID]):
    """Задание импорта.

    Переходы: ``pending → running → done | failed``. Повторная доставка задачи (ретрай
    воркера) допускает ``running → running``; завершённое задание повторно не выполняется —
    так задача ``catalog.import`` идемпотентна по ``import_id``.
    """

    def __init__(
        self,
        id: uuid.UUID,  # noqa: A002
        *,
        source: ImportSource,
        ref: str | None,
        file_id: str | None,
        actor_id: uuid.UUID | None,
        reason: str,
        idempotency_key: str | None,
        status: ImportStatus = ImportStatus.PENDING,
        counters: ImportCounters | None = None,
        error: str | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(id, version)
        self.source = source
        self.ref = ref
        self.file_id = file_id
        self.actor_id = actor_id
        self.reason = reason
        self.idempotency_key = idempotency_key
        self.status = status
        self.counters = counters or ImportCounters()
        self.error = error

    @property
    def is_finished(self) -> bool:
        """Задание завершено (успешно или нет)."""
        return self.status in {ImportStatus.DONE, ImportStatus.FAILED}

    def start(self) -> bool:
        """Перевести в ``running``.

        Returns:
            ``False``, если задание уже завершено и выполнять его не нужно.
        """
        if self.is_finished:
            return False
        self.status = ImportStatus.RUNNING
        return True

    def count(self, outcome: ItemOutcome) -> None:
        """Учесть итог по одной сущности."""
        self._require_running()
        current = getattr(self.counters, outcome.value)
        setattr(self.counters, outcome.value, current + 1)

    def finish(self) -> None:
        """Успешное завершение (частичные ошибки видны в ``counters.failed``)."""
        self._require_running()
        self.status = ImportStatus.DONE

    def fail(self, error_code: str) -> None:
        """Задание провалено целиком (источник выключен, недоступен, файл битый)."""
        if self.is_finished:
            return
        self.status = ImportStatus.FAILED
        self.error = error_code[:MAX_ERROR_LEN]

    def _require_running(self) -> None:
        if self.status is not ImportStatus.RUNNING:
            raise ConflictError("задание не выполняется", code="catalog.import_not_running")
