"""Пересборка индекса OpenSearch: новый физический индекс + атомарное переключение алиаса.

Адаптер порта ``IndexMigrator``. Запись идёт в конкретный индекс (не в алиас) с
``version_type=external``; боевой индекс не меняется до ``switch``. Старые индексы остаются —
откат одним ``rollback``; удаляются только руками (``DELETE /<index>``), когда новый проверен.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import structlog
from opensearchpy import AsyncOpenSearch
from opensearchpy.exceptions import NotFoundError as OsNotFoundError
from opensearchpy.exceptions import OpenSearchException

from catalog_service.application.dto import SearchDocument
from catalog_service.infrastructure.search.opensearch import (
    concrete_index,
    document_body,
    index_body,
)
from gv_core.errors import DependencyUnavailableError

log = structlog.get_logger(__name__)

_VERSION_RE = re.compile(r"_v(\d+)$")
_CONFLICT = 409
_ERROR_FROM = 300


def _unavailable() -> DependencyUnavailableError:
    return DependencyUnavailableError("OpenSearch недоступен", code="catalog.search_unavailable")


def index_number(name: str) -> int | None:
    """Номер версии из имени ``<alias>_vN`` или ``None``."""
    match = _VERSION_RE.search(name)
    return int(match.group(1)) if match else None


def bulk_lines(index: str, docs: Sequence[SearchDocument]) -> list[dict[str, Any]]:
    """Тело ``_bulk``: пары «действие, документ» с внешней версией агрегата."""
    lines: list[dict[str, Any]] = []
    for doc in docs:
        lines.append(
            {
                "index": {
                    "_index": index,
                    "_id": str(doc.id),
                    "version": doc.version,
                    "version_type": "external",
                }
            }
        )
        lines.append(document_body(doc))
    return lines


class OpenSearchMigrator:
    """``IndexMigrator`` на OpenSearch."""

    def __init__(self, client: AsyncOpenSearch, alias: str, *, target: str | None = None) -> None:
        self._client = client
        self._alias = alias
        self._target = target

    @property
    def target(self) -> str:
        """Целевой физический индекс (по умолчанию ``<alias>_v<INDEX_VERSION>``)."""
        return self._target or concrete_index(self._alias)

    async def live_indices(self) -> tuple[str, ...]:
        """Индексы за алиасом."""
        try:
            found = await self._client.indices.get_alias(name=self._alias)
        except OsNotFoundError:
            return ()
        except OpenSearchException as exc:
            raise _unavailable() from exc
        return tuple(sorted(found))

    async def prepare(self) -> None:
        """Создать целевой индекс, если его нет."""
        try:
            if not await self._client.indices.exists(index=self.target):
                await self._client.indices.create(index=self.target, body=index_body())
                log.info("catalog.reindex_index_created", index=self.target)
        except OpenSearchException as exc:
            raise _unavailable() from exc

    async def write(self, docs: Sequence[SearchDocument]) -> int:
        """Записать порцию; конфликт версий (уже есть не меньшая) — пропуск.

        При ошибке части порции остальное уже записано; повтор безопасен (внешняя версия).
        """
        if not docs:
            return 0
        try:
            response = await self._client.bulk(body=bulk_lines(self.target, docs))
        except OpenSearchException as exc:
            raise _unavailable() from exc
        written = 0
        for entry in response.get("items", []):
            result = entry.get("index", {})
            status = int(result.get("status", 500))
            if status == _CONFLICT:
                continue
            if status >= _ERROR_FROM:
                log.error(
                    "catalog.reindex_bulk_error", id=result.get("_id"), error=result.get("error")
                )
                raise DependencyUnavailableError(
                    "OpenSearch отклонил документ при переиндексации",
                    code="catalog.reindex_bulk_failed",
                )
            written += 1
        return written

    async def count(self) -> int:
        """Документов в целевом индексе."""
        try:
            await self._client.indices.refresh(index=self.target)
            result = await self._client.count(index=self.target)
        except OpenSearchException as exc:
            raise _unavailable() from exc
        return int(result["count"])

    async def _swap(self, remove: Sequence[str], add: str) -> None:
        actions: list[dict[str, Any]] = [
            {"remove": {"index": index, "alias": self._alias}} for index in remove if index != add
        ]
        actions.append({"add": {"index": add, "alias": self._alias}})
        try:
            await self._client.indices.update_aliases(body={"actions": actions})
        except OpenSearchException as exc:
            raise _unavailable() from exc

    async def switch(self) -> tuple[str, ...]:
        """Атомарно направить алиас на целевой индекс."""
        previous = await self.live_indices()
        await self._swap(previous, self.target)
        log.info("catalog.reindex_switched", target=self.target, previous=list(previous))
        return previous

    async def rollback(self) -> tuple[str, str]:
        """Вернуть алиас на ближайший более старый индекс."""
        live = await self.live_indices()
        if not live:
            raise DependencyUnavailableError(
                "у алиаса нет индекса", code="catalog.reindex_no_alias"
            )
        current = max(live, key=lambda name: index_number(name) or 0)
        number = index_number(current) or 0
        try:
            found = await self._client.indices.get(index=f"{self._alias}_v*")
        except OpenSearchException as exc:
            raise _unavailable() from exc
        older = [n for n in found if (index_number(n) or 0) < number]
        if not older:
            raise DependencyUnavailableError(
                "нет более старого индекса для отката", code="catalog.reindex_no_previous"
            )
        previous = max(older, key=lambda name: index_number(name) or 0)
        await self._swap(live, previous)
        log.info("catalog.reindex_rolled_back", current=current, previous=previous)
        return current, previous
