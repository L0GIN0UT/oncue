"""Догрузка авторов сборников в проекцию ``collection_authors`` (команда оператора).

Проекцию наполняет событие ``collections.CollectionPublished``, но сборники, опубликованные до
выката защиты от накрутки (``ugc_guard``), в ней отсутствуют: владелец неизвестен, и правило
«за свой сборник наград нет» для них не работает. Команда берёт автора из публичного каталога
collections (карточка сборника: ``kind``, ``author.user_id``) и пишет в проекцию. Повтор
безопасен (upsert). БД collections не читается; новых HTTP-ручек у progression нет.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import structlog

from gv_core.clock import Clock
from progression_service.application.ports import UnitOfWorkFactory

log = structlog.get_logger(__name__)

_BATCH = 200


@dataclass(frozen=True, slots=True)
class CollectionCard:
    """Сборник из публичного каталога: вид и автор (``None`` — кураторский или удалённый)."""

    collection_id: UUID
    kind: str
    author_id: UUID | None


class CollectionDirectory(Protocol):
    """Публичный каталог сборников collections (только чтение)."""

    def cards(self) -> AsyncIterator[CollectionCard]:
        """Все опубликованные сборники постранично."""
        ...


@dataclass(frozen=True, slots=True)
class BackfillReport:
    """Итог догрузки."""

    seen: int
    written: int
    skipped: int


class BackfillCollectionAuthors:
    """Дописать в проекцию сборники, которых в ней ещё нет (существующие не трогает)."""

    def __init__(
        self, uow: UnitOfWorkFactory, directory: CollectionDirectory, clock: Clock
    ) -> None:
        self._uow = uow
        self._directory = directory
        self._clock = clock

    async def __call__(self) -> BackfillReport:
        seen = written = 0
        batch: list[CollectionCard] = []
        async for card in self._directory.cards():
            seen += 1
            batch.append(card)
            if len(batch) >= _BATCH:
                written += await self._flush(batch)
                batch = []
        written += await self._flush(batch)
        log.info("progression.collection_authors_backfilled", seen=seen, written=written)
        return BackfillReport(seen=seen, written=written, skipped=seen - written)

    async def _flush(self, cards: list[CollectionCard]) -> int:
        if not cards:
            return 0
        async with self._uow() as uow:
            known = await uow.collection_authors.get_many(c.collection_id for c in cards)
            fresh = [c for c in cards if c.collection_id not in known]
            now = self._clock.now()
            for card in fresh:
                await uow.collection_authors.upsert(
                    card.collection_id, kind=card.kind, author_id=card.author_id, now=now
                )
            await uow.commit()
        return len(fresh)
