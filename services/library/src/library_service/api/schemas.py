"""Схемы публичного API library (план этапа 3b §2.5, D11–D12, ADR 0031).

Медиатека приватна: все ручки — только для пользователя (гость — 403
``auth.guest_forbidden``), чужая полка — 404 ``library.shelf_not_found``. Полка в пути —
``shelf_ref``: ``system:<key>`` (системная из конфига, создаётся лениво) или uuid своей.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Domain = Literal["music", "film"]
ShelfKind = Literal["system", "custom"]
SaveSource = Literal["reveal", "results", "card", "collection", "library"]
"""Экран, с которого сохранили или убрали единицу (поле ``source`` событий library)."""
SHELF_REF_PATTERN = (
    r"^(system:[a-z][a-z0-9_]{0,63}|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
ShelfRef = Annotated[str, StringConstraints(pattern=SHELF_REF_PATTERN)]
Cursor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,512}$")]
ShelfTitleIn = Annotated[str, StringConstraints(min_length=1, max_length=200)]
"""Сырое название своей полки; после ``gv_text.ugc`` — не длиннее ``limits.shelf_title_max``."""


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShelfOut(BaseModel):
    """Полка пользователя: системная (из конфига) или своя."""

    ref: str = Field(description="`system:<key>` или uuid своей полки")
    shelf_id: UUID | None = Field(
        default=None, description="null у системной полки, ещё не созданной лениво"
    )
    kind: ShelfKind
    key: str | None = Field(default=None, description="Ключ системной полки")
    title: str | None = Field(default=None, description="Название своей полки")
    title_key: str | None = Field(default=None, description="Ключ i18n системной полки")
    domains: list[Domain]
    exclusive_with: list[str] = Field(
        default_factory=list, description="Ключи системных полок, откуда единица уходит"
    )
    items_count: int = Field(ge=0)
    updated_at: datetime | None = None


class CreateShelfRequest(_Request):
    """Своя полка (≤ ``limits.custom_shelves_max``)."""

    title: ShelfTitleIn


class RenameShelfRequest(_Request):
    """Переименовать свою полку."""

    title: ShelfTitleIn


class ShelfItemOut(BaseModel):
    """Единица на полке: карточка из проекции catalog (без вызова catalog, D11)."""

    media_item_id: UUID
    domain: Domain
    kind: str = Field(description="track, film, series, anime")
    title: str
    year: int | None = None
    persons: list[str] = Field(default_factory=list, description="Исполнители или режиссёры")
    status: Literal["available", "unavailable"] = Field(
        description="unavailable — единица заблокирована в catalog (остаётся на полке)"
    )
    source: SaveSource
    added_at: datetime


class ShelfItemsPage(BaseModel):
    """Единицы полки (новые сверху, keyset-курсор)."""

    shelf: ShelfOut
    items: list[ShelfItemOut]
    next_cursor: str | None = None


class SaveItemRequest(_Request):
    """Сохранить единицу на полку."""

    source: SaveSource


class SaveResult(BaseModel):
    """Итог сохранения или удаления: на каких полках единица теперь лежит.

    ``changed = false`` — повтор (no-op, событие не публикуется, D12).
    """

    media_item_id: UUID
    changed: bool
    shelves: list[str] = Field(description="ref полок с этой единицей")
    removed_from: list[str] = Field(
        default_factory=list, description="ref полок, откуда убрал exclusive_with"
    )


class ItemShelvesOut(BaseModel):
    """На каких полках пользователя лежит единица (для кнопки «Сохранить»)."""

    media_item_id: UUID
    shelves: list[str]
