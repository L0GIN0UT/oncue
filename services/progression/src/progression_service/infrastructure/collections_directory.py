"""Публичный каталог сборников collections по HTTP: страницы ``GET /v1/collections``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx

from gv_core.errors import DependencyUnavailableError
from progression_service.application.admin.collection_authors import CollectionCard

_PAGE = 50  # максимум limit в контракте collections
_MAX_PAGES = 10_000  # защита от зацикливания курсора


class HttpCollectionDirectory:
    """Читает публичный каталог (без токена: ручка анонимная, ответы кэшируемые)."""

    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def cards(self) -> AsyncIterator[CollectionCard]:
        """Все опубликованные сборники, страница за страницей."""
        cursor: str | None = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for _ in range(_MAX_PAGES):
                params: dict[str, Any] = {"limit": _PAGE}
                if cursor:
                    params["cursor"] = cursor
                try:
                    response = await client.get(f"{self._base}/v1/collections", params=params)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise DependencyUnavailableError(
                        "каталог сборников недоступен", code="progression.collections_unavailable"
                    ) from exc
                page = response.json()
                for item in page["items"]:
                    author = item.get("author") or {}
                    raw = author.get("user_id")
                    yield CollectionCard(
                        collection_id=UUID(item["collection_id"]),
                        kind=item["kind"],
                        author_id=UUID(raw) if raw else None,
                    )
                cursor = page.get("next_cursor")
                if not cursor:
                    return
