"""JSON ответов медиатеки (форма — ``api/schemas.py``: ``ShelfOut``, ``SaveResult``, …).

Ответ мутации сохраняется вместе с ``Idempotency-Key`` (``gv_db.idempotency``), поэтому
use-cases возвращают готовый JSON, а не доменные объекты.
"""

from __future__ import annotations

from typing import Any

from library_service.domain.library import CatalogEntry, PlacementResult, Shelf, ShelfItem
from library_service.domain.shelves import ShelfCatalog, ShelfKind, ShelfRef, SystemShelfSpec

type Json = dict[str, Any]

AVAILABLE = "available"
UNAVAILABLE = "unavailable"


def system_shelf_out(spec: SystemShelfSpec, shelf: Shelf | None) -> Json:
    """Системная полка; ``shelf = None`` — ещё не создана (пустая, ``shelf_id: null``)."""
    return {
        "ref": str(ShelfRef.system(spec.key)),
        "shelf_id": str(shelf.id) if shelf is not None else None,
        "kind": str(ShelfKind.SYSTEM),
        "key": spec.key,
        "title": None,
        "title_key": spec.title_key,
        "domains": list(spec.domains),
        "exclusive_with": list(spec.exclusive_with),
        "items_count": shelf.items_count if shelf is not None else 0,
        "updated_at": shelf.updated_at.isoformat() if shelf is not None else None,
    }


def custom_shelf_out(catalog: ShelfCatalog, shelf: Shelf) -> Json:
    """Своя полка."""
    return {
        "ref": str(shelf.ref),
        "shelf_id": str(shelf.id),
        "kind": str(ShelfKind.CUSTOM),
        "key": None,
        "title": shelf.title,
        "title_key": None,
        "domains": list(catalog.custom_domains),
        "exclusive_with": [],
        "items_count": shelf.items_count,
        "updated_at": shelf.updated_at.isoformat(),
    }


def shelf_out(catalog: ShelfCatalog, spec: SystemShelfSpec | None, shelf: Shelf | None) -> Json:
    """Полка по результату ``UserLibrary.resolve``."""
    if spec is not None:
        return system_shelf_out(spec, shelf)
    assert shelf is not None  # noqa: S101 — своя полка всегда существует после resolve
    return custom_shelf_out(catalog, shelf)


def placement_out(result: PlacementResult) -> Json:
    """``SaveResult``."""
    return {
        "media_item_id": str(result.media_item_id),
        "changed": result.changed,
        "shelves": [str(r) for r in result.shelves],
        "removed_from": [str(r) for r in result.removed_from],
    }


def item_out(catalog: ShelfCatalog, item: ShelfItem, entry: CatalogEntry | None) -> Json:
    """Карточка единицы на полке. Заблокированная (или пропавшая из проекции) единица
    остаётся на полке со ``status: unavailable`` (D11)."""
    available = entry is not None and entry.available
    return {
        "media_item_id": str(item.media_item_id),
        "domain": entry.domain if entry is not None else item.domain,
        "kind": entry.kind if entry is not None else "",
        "title": entry.title if entry is not None else "",
        "year": entry.year if entry is not None else None,
        "persons": (
            entry.card_persons(catalog.card.person_roles, catalog.card.persons_max)
            if entry is not None
            else []
        ),
        "status": AVAILABLE if available else UNAVAILABLE,
        "source": item.source,
        "added_at": item.added_at.isoformat(),
    }
