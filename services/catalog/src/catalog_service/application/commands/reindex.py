"""Безопасная пересборка поискового индекса (``catalog reindex``).

Алгоритм без простоя и без потери данных:

1. ``prepare`` — создать целевой индекс ``<alias>_vN`` (боевой индекс не трогаем).
2. Основной проход — порциями по ключу ``id`` читаем актуальные строки БД и пишем в целевой
   индекс с ``version_type=external`` (версия агрегата). Повторная запись той же версии —
   конфликт и пропуск, поэтому команда идемпотентна и возобновляема (после обрыва её просто
   запускают снова).
3. Догоняющий проход — события, пришедшие во время основного прохода, писались только в
   старый индекс; повторный проход подтягивает их из БД.
4. Проверка полноты — документов в целевом индексе не меньше, чем единиц в БД; иначе
   алиас не переключается.
5. ``switch`` — один атомарный запрос ``_aliases`` (remove + add), затем сброс кэша
   подсказок. Старый индекс остаётся — откат одним ``rollback``.
6. Догоняющий проход после переключения закрывает окно между шагом 3 и 5.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog

from catalog_service.application.dto import SearchDocument
from catalog_service.application.ports import IndexMigrator, PrefixCache, UnitOfWorkFactory
from gv_core.errors import DependencyUnavailableError

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReindexReport:
    """Итог пересборки."""

    target: str
    previous: tuple[str, ...]
    seen: int
    written: int
    switched: bool
    already_live: bool


class RebuildIndex:
    """Пересобрать индекс в новый физический индекс и переключить алиас."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        migrator: IndexMigrator,
        cache: PrefixCache | None,
        *,
        batch: int = 500,
    ) -> None:
        if batch < 1:
            raise ValueError("batch должен быть положительным")
        self._uow = uow_factory
        self._migrator = migrator
        self._cache = cache
        self._batch = batch

    async def _pass(self) -> tuple[int, int, uuid.UUID | None]:
        """Один проход по всем единицам; вернуть (просмотрено, записано, последний id)."""
        seen = written = 0
        cursor: uuid.UUID | None = None
        while True:
            async with self._uow() as uow:
                ids = await uow.items.list_ids_after(None, cursor, self._batch)
                items = await uow.items.get_many(ids) if ids else []
            if not ids:
                return seen, written, cursor
            docs = [SearchDocument.from_item(i) for i in items]
            written += await self._migrator.write(docs)
            seen += len(items)
            cursor = ids[-1]
            log.info("catalog.reindex_progress", seen=seen, written=written, last_id=str(cursor))

    async def sync(self) -> int:
        """Один проход «БД → индекс» без переключения алиаса; вернуть число записанных.

        Нужен после отката: события, пришедшие после переключения, писались только в новый
        индекс, и прежний надо догнать из БД.
        """
        await self._migrator.prepare()
        _, written, _ = await self._pass()
        if self._cache is not None:
            await self._cache.invalidate()
        return written

    async def __call__(self, *, switch: bool = True) -> ReindexReport:
        """Выполнить пересборку.

        Повторный запуск после обрыва безопасен: уже записанные версии пропускаются.

        Args:
            switch: ``False`` — только собрать индекс, алиас не трогать (репетиция).

        Raises:
            DependencyUnavailableError: ``catalog.reindex_incomplete`` — в целевом индексе
                меньше документов, чем единиц в БД; алиас не переключён.
        """
        previous = await self._migrator.live_indices()
        already_live = self._migrator.target in previous
        await self._migrator.prepare()
        _, written, _ = await self._pass()
        # Догоняющий проход: события, пришедшие во время основного, писались в старый индекс.
        total, c_written, _ = await self._pass()
        written += c_written
        indexed = await self._migrator.count()
        if indexed < total:
            raise DependencyUnavailableError(
                f"в индексе {indexed} документов из {total}; алиас не переключён",
                code="catalog.reindex_incomplete",
            )
        switched = False
        if switch and not already_live:
            previous = await self._migrator.switch()
            switched = True
            # Алиас уже переключён: сбой дальше не должен выглядеть как провал пересборки.
            try:
                if self._cache is not None:
                    await self._cache.invalidate()
                # Окно между догоняющим проходом и переключением: события писались в старый.
                _, c_written, _ = await self._pass()
                written += c_written
            except DependencyUnavailableError:
                log.exception("catalog.reindex_post_switch_failed")
        log.info(
            "catalog.reindex_done",
            target=self._migrator.target,
            seen=total,
            written=written,
            switched=switched,
        )
        return ReindexReport(
            target=self._migrator.target,
            previous=previous,
            seen=total,
            written=written,
            switched=switched,
            already_live=already_live,
        )
