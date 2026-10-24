"""Комментарии к сборникам, жалобы, свои санкции (5c, поток F2; ADR 0051, 0052).

Шаг 0a: ручки объявлены, реализация - потоки F2 и F3; до тех пор отвечают 501
``common.not_implemented``.

* чтение комментариев открыто всем (в т.ч. гостю); запись, жалобы и санкции - аккаунт;
* комментарии: плоские, ≤ 500 символов, `gv_text.ugc`, лимит в минуту, правка 5 минут (значения
  в конфиге); текст в события не попадает;
* санкция `CHAT` отвечает 403 `social.chat_muted`;
* жалоба идемпотентна по `(автор, объект)`: повтор - 200 `repeated = true`.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки доступа.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from gv_auth import Principal, optional_principal, require_user
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, problem_responses
from social_service.api.schemas import (
    CommentBody,
    CommentPage,
    CommentView,
    Cursor,
    MySanctions,
    ReportCreate,
    ReportReasons,
    ReportResult,
)

User = Annotated[Principal, Depends(require_user)]
Viewer = Annotated[Principal | None, Depends(optional_principal)]
CollectionIdPath = Annotated[UUID, Path(description="Идентификатор сборника")]
CommentIdPath = Annotated[UUID, Path(description="Идентификатор комментария")]


def _stub() -> NotImplementedYetError:
    return NotImplementedYetError("ручка появится в потоках F2/F3 (5c)")


def register_discussion_routes(router: APIRouter) -> None:
    """Добавить в ``/v1`` ручки комментариев, жалоб и своих санкций."""

    @router.get(
        "/collections/{collection_id}/comments",
        response_model=CommentPage,
        summary="Комментарии сборника",
        description=(
            "Опубликованный сборник, курсор, новые первыми. Удалённые не возвращаются. "
            "`can_edit`/`can_delete` - для зрителя (для гостя `false`). Неопубликованный "
            "или неизвестный сборник - 404 `social.collection_not_found`."
        ),
        responses=problem_responses(404, 501),
    )
    async def list_comments(
        collection_id: CollectionIdPath,
        viewer: Viewer,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> CommentPage:
        raise _stub()

    @router.post(
        "/collections/{collection_id}/comments",
        response_model=CommentView,
        status_code=201,
        summary="Написать комментарий",
        description=(
            "Событие `CommentPosted`. Ошибки: 422 `social.comment_rejected` (denylist), 429 "
            "`social.comments_rate_limit`, 403 `social.chat_muted` (санкция `CHAT`), 404 "
            "`social.collection_not_found`. Повтор по `Idempotency-Key` - тот же комментарий."
        ),
        responses=problem_responses(401, 403, 404, 422, 429, 501),
    )
    async def post_comment(
        collection_id: CollectionIdPath, body: CommentBody, user: User, key: IdempotencyKey
    ) -> CommentView:
        raise _stub()

    @router.patch(
        "/comments/{comment_id}",
        response_model=CommentView,
        summary="Изменить свой комментарий",
        description=(
            "Только автор, не позже `comments.edit_window_s` (5 минут): иначе 409 "
            "`social.comment_edit_expired`. Чужой комментарий - 404."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def edit_comment(comment_id: CommentIdPath, body: CommentBody, user: User) -> CommentView:
        raise _stub()

    @router.delete(
        "/comments/{comment_id}",
        status_code=204,
        summary="Удалить комментарий",
        description=(
            "Автор, автор сборника или модератор (`social.comments.delete_any`); событие "
            "`CommentDeleted`. Повтор - 204; остальным - 404."
        ),
        responses=problem_responses(401, 403, 404, 501),
    )
    async def delete_comment(comment_id: CommentIdPath, user: User) -> None:
        raise _stub()

    @router.get(
        "/report-reasons",
        response_model=ReportReasons,
        summary="Причины жалоб",
        description="Справочник из конфига; `target_kinds` - к каким объектам причина применима.",
        responses=problem_responses(501),
    )
    async def report_reasons() -> ReportReasons:
        raise _stub()

    @router.post(
        "/reports",
        response_model=ReportResult,
        status_code=201,
        summary="Пожаловаться",
        description=(
            "Игрок из матча (`context_match_id` обязателен, общий несоло-матч; иначе 422 "
            "`social.report_no_shared_match`), сборник, комментарий, аватарка. Одна жалоба на "
            "`(автор, объект)`: повтор - 200 `repeated = true`. Нельзя жаловаться на себя (422). "
            "Порог жалоб за сутки по объекту открывает кейс модерации. Текст не попадает в "
            "события. Событие `ReportFiled`. Лимит жалоб в сутки: 429."
        ),
        responses=problem_responses(401, 403, 404, 422, 429, 501),
    )
    async def file_report(body: ReportCreate, user: User, key: IdempotencyKey) -> ReportResult:
        raise _stub()

    @router.get(
        "/me/sanctions",
        response_model=MySanctions,
        summary="Мои санкции",
        description="Действующие ограничения со сроком и причиной (экран санкции).",
        responses=problem_responses(401, 403, 501),
    )
    async def my_sanctions(user: User) -> MySanctions:
        raise _stub()
