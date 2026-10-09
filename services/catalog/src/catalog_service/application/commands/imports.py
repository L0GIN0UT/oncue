"""Импорт метаданных: постановка задания (admin-API) и его выполнение (воркер).

Поток: ``POST /admin/v1/imports`` → ``RequestImport`` создаёт ``ImportJob`` и ставит
RabbitMQ-задачу ``catalog.import`` (``task_id = import_id``) → воркер вызывает ``RunImport``
→ каждая единица сохраняется ``UpsertImportedItem`` в своей транзакции вместе с событием
в outbox. Повторная доставка задачи безопасна: завершённое задание не выполняется, а
единицы сопоставляются по внешним идентификаторам (повторный импорт — ``unchanged``).

Источники (TMDB, файл, музыкальные каталоги) — реестр ``Mapping[ImportSource,
ImportEntrySource]`` из composition root; источник, которого нет в реестре, выключен.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import structlog

from catalog_service.application.dto import EXTERNAL_MATCH_ORDER, ImportedItem
from catalog_service.application.ports import (
    CatalogUnitOfWork,
    ImportEntrySource,
    TaskQueue,
    UnitOfWorkFactory,
)
from catalog_service.domain.import_job import ImportJob, ImportSource, ItemOutcome
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import ItemData, PersonCredit
from catalog_service.domain.normalization import TitleNormalizer
from gv_core.clock import Clock
from gv_core.errors import (
    AppError,
    ConflictError,
    DependencyUnavailableError,
    FeatureDisabledError,
)
from gv_core.ids import uuid7

log = structlog.get_logger(__name__)

IMPORT_TASK = "import"
"""Тип RabbitMQ-задачи; очередь — ``catalog.import``."""

IMPORT_CRASHED = "catalog.import_crashed"
"""Код ошибки задания, упавшего с неожиданным исключением."""

type ImportSources = Mapping[ImportSource, ImportEntrySource]


@dataclass(frozen=True, slots=True)
class ImportCommand:
    """Запрос импорта от администратора."""

    source: ImportSource
    ref: str | None
    file_id: str | None
    reason: str
    actor_id: uuid.UUID
    idempotency_key: str


def import_job_view(job: ImportJob) -> dict[str, Any]:
    """Представление задания для API."""
    return {
        "import_id": str(job.id),
        "source": job.source.value,
        "ref": job.ref,
        "file_id": job.file_id,
        "status": job.status.value,
        "counters": job.counters.as_dict(),
        "error": job.error,
    }


def _source_disabled(source: ImportSource) -> FeatureDisabledError:
    return FeatureDisabledError(f"источник {source.value} выключен", code="catalog.source_disabled")


class RequestImport:
    """Создать задание импорта и поставить задачу воркеру (право ``catalog.import``)."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        tasks: TaskQueue,
        sources: ImportSources,
        new_id: Callable[[], uuid.UUID] = uuid7,
    ) -> None:
        self._uow = uow_factory
        self._tasks = tasks
        self._sources = sources
        self._new_id = new_id

    async def __call__(self, cmd: ImportCommand) -> dict[str, Any]:
        """Вернуть представление задания (новое или ранее созданное с этим ключом).

        Raises:
            FeatureDisabledError: источник выключен (нет в реестре источников).
            InvalidInputError: неверный ``ref``/``file_id``.
            ConflictError: ключ идемпотентности использован с другими параметрами.
            DependencyUnavailableError: не удалось поставить задачу (повторите запрос
                с тем же ключом — задача будет поставлена снова).
        """
        source = self._sources.get(cmd.source)
        if source is None:
            raise _source_disabled(cmd.source)
        source.validate(cmd.ref, cmd.file_id)
        async with self._uow() as uow:
            job = await uow.imports.find_by_key(cmd.actor_id, cmd.idempotency_key)
            if job is not None:
                if (job.source, job.ref, job.file_id) != (cmd.source, cmd.ref, cmd.file_id):
                    raise ConflictError(
                        "Idempotency-Key уже использован с другим запросом",
                        code="idempotency.key_reused",
                    )
            else:
                job = ImportJob(
                    self._new_id(),
                    source=cmd.source,
                    ref=cmd.ref,
                    file_id=cmd.file_id,
                    actor_id=cmd.actor_id,
                    reason=cmd.reason,
                    idempotency_key=cmd.idempotency_key,
                )
                await uow.imports.add(job)
                await uow.commit()
                log.info("catalog.import_requested", import_id=str(job.id), source=job.source)
        if not job.is_finished:
            # Повтор с тем же ключом переставляет незавершённую задачу: если прошлая
            # постановка упала после коммита, задание не зависнет в pending.
            try:
                await self._tasks.enqueue(
                    IMPORT_TASK, {"import_id": str(job.id)}, task_id=str(job.id)
                )
            except Exception as exc:
                log.warning("catalog.import_enqueue_failed", import_id=str(job.id), exc_info=True)
                raise DependencyUnavailableError(
                    "очередь задач недоступна", code="catalog.tasks_unavailable"
                ) from exc
        return import_job_view(job)


class UpsertImportedItem:
    """Создать или обновить единицу по данным импорта (одна транзакция с outbox)."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        normalizer: TitleNormalizer,
        clock: Clock,
        new_id: Callable[[], uuid.UUID] = uuid7,
    ) -> None:
        self._uow = uow_factory
        self._normalizer = normalizer
        self._clock = clock
        self._new_id = new_id

    async def __call__(self, imported: ImportedItem) -> ItemOutcome:
        """Сохранить единицу и вернуть итог (``created``/``updated``/``unchanged``)."""
        current_year = self._clock.now().year
        async with self._uow() as uow:
            data = await self._resolve(uow, imported)
            item = await self._find_existing(uow, imported)
            if item is None:
                item = MediaItem.create(
                    self._new_id(), data, self._normalizer, current_year=current_year
                )
                await uow.items.add(item)
                outcome = ItemOutcome.CREATED
            elif item.apply_import(data, self._normalizer, current_year=current_year):
                await uow.items.save(item)
                outcome = ItemOutcome.UPDATED
            else:
                return ItemOutcome.UNCHANGED
            uow.track(item)
            await uow.commit()
        return outcome

    @staticmethod
    async def _find_existing(uow: CatalogUnitOfWork, imported: ImportedItem) -> MediaItem | None:
        for source in EXTERNAL_MATCH_ORDER:
            ref = imported.external_refs.get(source)
            if ref:
                item = await uow.items.find_by_external_ref(source, ref)
                if item is not None:
                    return item
        return None

    @staticmethod
    async def _resolve(uow: CatalogUnitOfWork, imported: ImportedItem) -> ItemData:
        persons: list[PersonCredit] = []
        for p in imported.persons:
            person_id = await uow.persons.resolve(p.key, p.name)
            persons.append(PersonCredit(person_id, p.name, p.role, p.order))
        return ItemData(
            kind=imported.kind,
            title=imported.title,
            original_title=imported.original_title,
            year=imported.year,
            aliases=imported.aliases,
            persons=tuple(persons),
            genres=imported.genres,
            external_refs=dict(imported.external_refs),
            metrics=imported.metrics,
            source_media=imported.source_media,
        )


class RunImport:
    """Выполнить задание импорта (обработчик задачи ``catalog.import``)."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        upsert: UpsertImportedItem,
        sources: ImportSources,
    ) -> None:
        self._uow = uow_factory
        self._upsert = upsert
        self._sources = sources

    async def __call__(self, import_id: uuid.UUID) -> None:
        """Выполнить задание.

        Ошибка приложения (``AppError``: источник выключен, недоступен, нет объекта) —
        задание ``failed`` с её ``code``, исключение не бросается. Неожиданное исключение —
        задание ``failed`` с ``catalog.import_crashed`` (сохраняется отдельной
        транзакцией), а исключение пробрасывается: так задание не остаётся вечно
        ``running``, а ошибка видна воркеру и в логах.
        """
        async with self._uow() as uow:
            job = await uow.imports.get(import_id)
            if job is None:
                log.warning("catalog.import_unknown", import_id=str(import_id))
                return
            if not job.start():
                log.info("catalog.import_already_finished", import_id=str(import_id))
                return
            await uow.imports.save(job)
            await uow.commit()

        try:
            await self._consume(job)
        except AppError as exc:
            job.fail(exc.code)
        except Exception:
            log.exception("catalog.import_crashed", import_id=str(job.id))
            job.fail(IMPORT_CRASHED)
            await self._save(job)
            raise
        else:
            job.finish()
        log.info(
            "catalog.import_finished",
            import_id=str(job.id),
            status=job.status,
            **job.counters.as_dict(),
        )
        await self._save(job)

    async def _consume(self, job: ImportJob) -> None:
        source = self._sources.get(job.source)
        if source is None:
            raise _source_disabled(job.source)
        async for entry in source.entries(job):
            if isinstance(entry, ValueError):
                log.warning("catalog.import_bad_line", import_id=str(job.id), error=str(entry))
                job.count(ItemOutcome.FAILED)
                continue
            job.count(await self._upsert_one(job, entry))

    async def _save(self, job: ImportJob) -> None:
        async with self._uow() as uow:
            await uow.imports.save(job)
            await uow.commit()

    async def _upsert_one(self, job: ImportJob, entry: ImportedItem) -> ItemOutcome:
        try:
            return await self._upsert(entry)
        except AppError as exc:
            # Битая единица (год вне диапазона, конфликт внешнего id) не валит весь импорт.
            log.warning("catalog.import_item_failed", import_id=str(job.id), code=exc.code)
            return ItemOutcome.FAILED
