"""Admin API (/admin/v1): для админ-панели, только с правами модератора/администратора.

Каждая ручка защищена ``require_perm`` (минимум ``admin.access``). Мутации требуют
заголовок ``Idempotency-Key`` (``gv_service.IdempotencyKey``) и причину ``reason``
(``gv_service.Reason``); аудит — ``admin_actions``/``import_jobs``.
"""

# ruff: noqa: ARG001 — заглушки этапа 3a (501) объявляют параметры контракта до реализации.

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from catalog_service.application.commands.admin import AdminContext
from catalog_service.application.commands.imports import ImportCommand
from catalog_service.application.services import CatalogServices
from catalog_service.domain.import_job import ImportSource
from catalog_service.domain.model import AliasDraft, AliasKind, MediaDomain
from gv_auth import Principal, require_perm
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, Reason, problem_responses

# Ошибки, общие для всех ручек admin-API.
_AUTH = (401, 403, 422)
_READ = problem_responses(*_AUTH)
_READ_ONE = problem_responses(*_AUTH, 404)
_MUTATE = problem_responses(*_AUTH, 404, 409)
_IMPORT = problem_responses(*_AUTH, 409, 503)


class AliasOut(BaseModel):
    """Алиас с источником."""

    text: str
    normalized: str
    lang: str | None
    kind: str
    source: Literal["import", "manual"]


class AdminItem(BaseModel):
    """Карточка единицы для админки."""

    id: uuid.UUID
    domain: MediaDomain
    kind: str
    title: str
    original_title: str | None
    year: int | None
    status: Literal["active", "blocked"]
    blocked_reason_code: str | None
    version: int
    subtitle: str | None
    aliases: list[AliasOut]
    persons: list[dict[str, Any]]
    genres: list[str]
    external_refs: dict[str, str]
    metrics: dict[str, float | int]
    source_media: list[dict[str, Any]]


class AdminItemSummary(BaseModel):
    """Строка списка."""

    id: uuid.UUID
    domain: MediaDomain
    kind: str
    title: str
    year: int | None
    status: str
    version: int


class AdminItemPage(BaseModel):
    """Страница списка с курсором."""

    items: list[AdminItemSummary]
    next_cursor: str | None


class BlockRequest(BaseModel):
    """Блокировка."""

    reason: Reason
    reason_code: str = Field(default="moderation", max_length=64, pattern=r"^[a-z][a-z0-9_.]*$")


class UnblockRequest(BaseModel):
    """Разблокировка."""

    reason: Reason


class AliasIn(BaseModel):
    """Алиас от модератора."""

    text: str = Field(min_length=1, max_length=300)
    kind: AliasKind = AliasKind.ALT
    lang: str | None = Field(default=None, max_length=8)


class ReplaceAliasesRequest(BaseModel):
    """Новый полный список алиасов (все станут ручными)."""

    aliases: list[AliasIn] = Field(min_length=1, max_length=100)
    reason: Reason


class ImportRequest(BaseModel):
    """Запрос импорта: ``ref`` (id источника или ``search:<текст>``) либо ``file_id``."""

    source: ImportSource
    ref: str | None = Field(default=None, max_length=200)
    file_id: str | None = Field(default=None, max_length=100)
    reason: Reason


class ImportCounters(BaseModel):
    """Счётчики задания."""

    created: int
    updated: int
    unchanged: int
    failed: int


class ImportView(BaseModel):
    """Состояние задания импорта."""

    import_id: uuid.UUID
    source: ImportSource
    ref: str | None
    file_id: str | None
    status: Literal["pending", "running", "done", "failed"]
    counters: ImportCounters
    error: str | None


class ClueTextsOut(BaseModel):
    """Тексты загадок единицы (ADR 0024): в событие уходят только одобренные."""

    plot_raw: str | None = Field(description="Исходный сюжет (TMDB или ввод модератора)")
    plot_masked: str | None
    tagline: str | None
    emoji: str | None
    status: Literal["draft", "approved"]
    revision: int = Field(ge=0)


class ClueTextsIn(BaseModel):
    """Правка текстов загадок: после правки тексты снова требуют одобрения."""

    plot_masked: str | None = Field(default=None, min_length=1, max_length=2000)
    tagline: str | None = Field(default=None, min_length=1, max_length=300)
    emoji: str | None = Field(default=None, min_length=1, max_length=64)
    base_revision: int = Field(ge=0)
    reason: Reason


class ApproveClueTextsRequest(BaseModel):
    """Одобрить тексты ревизии ``revision``."""

    revision: int = Field(ge=1)
    reason: Reason


class RepublishRequest(BaseModel):
    """Переопубликовать единицы (заполнить новые поля у потребителей)."""

    domain: MediaDomain | None = None
    reason: Reason


class RepublishOut(BaseModel):
    """Задача переопубликации поставлена."""

    job_id: uuid.UUID


STAGE_3A = "этап 3a: поток M (docs/plans/stage-3a.md)"

# Права проверяются на каждой ручке; aliases на уровне модуля — FastAPI разрешает
# отложенные аннотации (from __future__ import annotations) только по глобальным именам.
Viewer = Annotated[Principal, Depends(require_perm("admin.access"))]
Blocker = Annotated[Principal, Depends(require_perm("admin.access", "catalog.block_item"))]
Editor = Annotated[Principal, Depends(require_perm("admin.access", "catalog.edit_aliases"))]
Importer = Annotated[Principal, Depends(require_perm("admin.access", "catalog.import"))]


def build_admin_router(c: CatalogServices) -> APIRouter:
    """Админские ручки каталога."""
    router = APIRouter(prefix="/admin/v1", tags=["catalog-admin"])

    @router.get("/items", response_model=AdminItemPage, summary="Список единиц", responses=_READ)
    async def list_items(
        _: Viewer,
        q: Annotated[str | None, Query(max_length=64)] = None,
        domain: MediaDomain | None = None,
        status: Literal["active", "blocked"] | None = None,
        cursor: Annotated[str | None, Query(max_length=1024)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> AdminItemPage:
        page = await c.list_items(q, domain, status, cursor, limit)
        return AdminItemPage(
            items=[AdminItemSummary(**vars_of(i)) for i in page.items],
            next_cursor=page.next_cursor,
        )

    @router.get(
        "/items/{item_id}",
        response_model=AdminItem,
        summary="Карточка единицы",
        responses=_READ_ONE,
    )
    async def get_item(item_id: uuid.UUID, _: Viewer) -> AdminItem:
        return AdminItem.model_validate(await c.get_item.admin(item_id))

    @router.post(
        "/items/{item_id}/block",
        response_model=AdminItem,
        summary="Заблокировать",
        responses=_MUTATE,
    )
    async def block(
        item_id: uuid.UUID, body: BlockRequest, principal: Blocker, key: IdempotencyKey
    ) -> AdminItem:
        ctx = AdminContext(principal.user_id, key, body.reason)
        return AdminItem.model_validate(await c.block_item(item_id, body.reason_code, ctx))

    @router.post(
        "/items/{item_id}/unblock",
        response_model=AdminItem,
        summary="Разблокировать",
        responses=_MUTATE,
    )
    async def unblock(
        item_id: uuid.UUID, body: UnblockRequest, principal: Blocker, key: IdempotencyKey
    ) -> AdminItem:
        ctx = AdminContext(principal.user_id, key, body.reason)
        return AdminItem.model_validate(await c.unblock_item(item_id, ctx))

    @router.put(
        "/items/{item_id}/aliases",
        response_model=AdminItem,
        summary="Заменить алиасы",
        responses=_MUTATE,
    )
    async def replace_aliases(
        item_id: uuid.UUID, body: ReplaceAliasesRequest, principal: Editor, key: IdempotencyKey
    ) -> AdminItem:
        ctx = AdminContext(principal.user_id, key, body.reason)
        drafts = [AliasDraft(a.text, a.kind, a.lang) for a in body.aliases]
        return AdminItem.model_validate(await c.replace_aliases(item_id, drafts, ctx))

    @router.get(
        "/items/{item_id}/clue-texts",
        response_model=ClueTextsOut,
        summary="Тексты загадок единицы",
        responses=problem_responses(*_AUTH, 404, 501),
    )
    async def get_clue_texts(item_id: uuid.UUID, _: Viewer) -> ClueTextsOut:
        raise NotImplementedYetError(STAGE_3A)

    @router.put(
        "/items/{item_id}/clue-texts",
        response_model=ClueTextsOut,
        summary="Изменить тексты загадок",
        responses=problem_responses(*_AUTH, 404, 409, 501),
    )
    async def put_clue_texts(
        item_id: uuid.UUID, body: ClueTextsIn, principal: Editor, key: IdempotencyKey
    ) -> ClueTextsOut:
        raise NotImplementedYetError(STAGE_3A)

    @router.post(
        "/items/{item_id}/clue-texts/approve",
        response_model=ClueTextsOut,
        summary="Одобрить тексты загадок",
        description="Одобренные тексты уходят в `catalog.MediaItemUpdated.clue_texts`.",
        responses=problem_responses(*_AUTH, 404, 409, 501),
    )
    async def approve_clue_texts(
        item_id: uuid.UUID, body: ApproveClueTextsRequest, principal: Editor, key: IdempotencyKey
    ) -> ClueTextsOut:
        raise NotImplementedYetError(STAGE_3A)

    @router.post(
        "/items/republish",
        response_model=RepublishOut,
        status_code=202,
        summary="Переопубликовать единицы",
        description="Фоновая задача: `MediaItemUpdated` с `version+1` для всех единиц домена.",
        responses=problem_responses(*_AUTH, 409, 501),
    )
    async def republish_items(
        body: RepublishRequest, principal: Importer, key: IdempotencyKey
    ) -> RepublishOut:
        raise NotImplementedYetError(STAGE_3A)

    @router.post(
        "/imports",
        response_model=ImportView,
        status_code=202,
        summary="Запустить импорт",
        responses=_IMPORT,
    )
    async def request_import(
        body: ImportRequest, principal: Importer, key: IdempotencyKey
    ) -> ImportView:
        view = await c.request_import(
            ImportCommand(
                source=body.source,
                ref=body.ref,
                file_id=body.file_id,
                reason=body.reason,
                actor_id=principal.user_id,
                idempotency_key=key,
            )
        )
        return ImportView.model_validate(view)

    @router.get(
        "/imports/{import_id}",
        response_model=ImportView,
        summary="Состояние импорта",
        responses=_READ_ONE,
    )
    async def get_import(import_id: uuid.UUID, _: Importer) -> ImportView:
        return ImportView.model_validate(await c.get_import(import_id))

    return router


def vars_of(obj: Any) -> dict[str, Any]:
    """Поля dataclass со ``slots`` как словарь."""
    return {name: getattr(obj, name) for name in obj.__dataclass_fields__}
