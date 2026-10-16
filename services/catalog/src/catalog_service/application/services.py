"""Набор use-cases, которыми пользуется HTTP API (собирается в composition root ``main``).

API не импортирует инфраструктуру (import-linter: ``api`` и ``infrastructure`` — соседние
слои), поэтому зависит от этого протокола, а реализацию подставляет ``main.py``.
"""

from __future__ import annotations

from typing import Protocol

from catalog_service.application.commands.admin import (
    ApproveClueTexts,
    BlockItem,
    EditClueTexts,
    GetClueTexts,
    ReplaceAliases,
    UnblockItem,
)
from catalog_service.application.commands.imports import RequestImport
from catalog_service.application.commands.republish import RequestRepublish
from catalog_service.application.ports import RateLimiter
from catalog_service.application.queries.search import (
    Autocomplete,
    GetImport,
    GetItem,
    ListItems,
    Search,
)


class CatalogServices(Protocol):
    """Use-cases и сервисные зависимости HTTP API."""

    @property
    def service_name(self) -> str:
        """Имя сервиса."""
        ...

    @property
    def rate_limiter(self) -> RateLimiter | None:
        """Ограничитель частоты публичного поиска (``None`` — выключен)."""
        ...

    @property
    def autocomplete(self) -> Autocomplete:
        """Автокомплит."""
        ...

    @property
    def search(self) -> Search:
        """Поиск."""
        ...

    @property
    def get_item(self) -> GetItem:
        """Карточка."""
        ...

    @property
    def list_items(self) -> ListItems:
        """Список для админки."""
        ...

    @property
    def get_import(self) -> GetImport:
        """Состояние импорта."""
        ...

    @property
    def block_item(self) -> BlockItem:
        """Блокировка."""
        ...

    @property
    def unblock_item(self) -> UnblockItem:
        """Разблокировка."""
        ...

    @property
    def replace_aliases(self) -> ReplaceAliases:
        """Замена алиасов."""
        ...

    @property
    def request_import(self) -> RequestImport:
        """Постановка импорта."""
        ...

    @property
    def get_clue_texts(self) -> GetClueTexts:
        """Тексты загадок (чтение)."""
        ...

    @property
    def edit_clue_texts(self) -> EditClueTexts:
        """Правка текстов загадок."""
        ...

    @property
    def approve_clue_texts(self) -> ApproveClueTexts:
        """Одобрение текстов загадок."""
        ...

    @property
    def request_republish(self) -> RequestRepublish:
        """Постановка переопубликации."""
        ...
