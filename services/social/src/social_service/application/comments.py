"""Комментарии к сборникам: публикация, правка, удаление, чтение (5c, ADR 0051).

Лимиты, окно правки и длина - из конфига действующей версии. Текст проверяет ``gv_text.ugc``
(denylist, нормализация); в события текст не попадает.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from gv_auth import Sanction
from gv_core.clock import Clock
from gv_core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
)
from gv_core.pagination import clamp_limit, decode_cursor, encode_cursor
from gv_db.idempotency import request_fingerprint, run_idempotent
from gv_text.ugc import UgcPolicy, UgcViolation
from social_service.application.config_access import current_config
from social_service.application.ports.config import ConfigProvider
from social_service.application.ports.discussion import (
    DiscussionUnitOfWork,
    DiscussionUowFactory,
    RateLimiter,
)
from social_service.application.ports.sanctions import SanctionReader
from social_service.domain import discussion as rules
from social_service.domain.config import SocialConfig
from social_service.domain.discussion import Comment
from social_service.domain.discussion_events import CommentDeleted, CommentPosted

FALLBACK_NICKNAME = "Игрок"
"""Ник, если автора нет в проекции пользователей (ещё не дошло событие или аккаунт удалён)."""
COMMENTS_WINDOW_S = 60
"""Окно лимита ``comments.per_minute`` - минута по определению параметра."""
TITLE_MAX = 120
MUTING = Sanction.CHAT_MUTE | Sanction.COMMENTS
"""Санкция ``CHAT`` для комментариев: чат выключен или комментарии выключены."""


@dataclass(frozen=True, slots=True)
class Actor:
    """Кто действует: идентификатор, права и признак отключённых комментариев/чата."""

    user_id: UUID
    perms: frozenset[str] = field(default_factory=frozenset)
    muted: bool = False


@dataclass(frozen=True, slots=True)
class CommentDto:
    """Комментарий для ответа зрителю."""

    id: UUID
    collection_id: UUID
    author_id: UUID
    author_nickname: str
    body: str
    created_at: datetime
    edited_at: datetime | None
    can_edit: bool
    can_delete: bool

    def to_json(self) -> dict[str, Any]:
        """JSON для хранилища идемпотентности."""
        return {
            "id": str(self.id),
            "collection_id": str(self.collection_id),
            "author_id": str(self.author_id),
            "author_nickname": self.author_nickname,
            "body": self.body,
            "created_at": self.created_at.isoformat(),
            "edited_at": self.edited_at.isoformat() if self.edited_at else None,
            "can_edit": self.can_edit,
            "can_delete": self.can_delete,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CommentDto:
        """Восстановить из JSON хранилища идемпотентности."""
        edited = data["edited_at"]
        return cls(
            id=UUID(data["id"]),
            collection_id=UUID(data["collection_id"]),
            author_id=UUID(data["author_id"]),
            author_nickname=data["author_nickname"],
            body=data["body"],
            created_at=datetime.fromisoformat(data["created_at"]),
            edited_at=datetime.fromisoformat(edited) if edited else None,
            can_edit=data["can_edit"],
            can_delete=data["can_delete"],
        )


@dataclass(frozen=True, slots=True)
class CommentPageDto:
    """Страница комментариев."""

    items: list[CommentDto]
    next_cursor: str | None
    total: int


class CommentService:
    """Use-cases комментариев."""

    def __init__(  # noqa: PLR0917 - порты, собираются в composition root
        self,
        uow_factory: DiscussionUowFactory,
        config: ConfigProvider,
        ugc: UgcPolicy,
        limiter: RateLimiter,
        clock: Clock,
        new_id: Callable[[], UUID],
        sanctions: SanctionReader,
    ) -> None:
        self._sanctions = sanctions
        self._uow = uow_factory
        self._config = config
        self._ugc = ugc
        self._limiter = limiter
        self._clock = clock
        self._new_id = new_id

    def _check_text(self, raw: str, cfg: SocialConfig) -> str:
        try:
            return self._ugc.check(raw, max_len=cfg.comments.max_length)
        except UgcViolation as exc:
            # Само слово в ответ не попадает - только машинный код нарушения.
            raise InvalidInputError(
                "комментарий не прошёл проверку",
                code=rules.COMMENT_REJECTED,
                details={"violation": exc.code},
            ) from exc

    async def _ensure_not_muted(self, actor: Actor) -> None:
        """Санкция ``CHAT`` по эффективной маске: claim токена или действующие санкции в БД."""
        if actor.muted or int(await self._sanctions.active_mask(actor.user_id)) & int(MUTING):
            raise ForbiddenError("комментарии отключены санкцией", code=rules.CHAT_MUTED)

    @staticmethod
    async def _ensure_not_blocked(
        uow: DiscussionUnitOfWork, actor: UUID, collection_author: UUID | None
    ) -> None:
        """Блокировка закрывает комментирование в обе стороны (поправка владельца 5c)."""
        if collection_author is None or collection_author == actor:
            return
        if await uow.blocks.is_blocked(collection_author, actor) or await uow.blocks.is_blocked(
            actor, collection_author
        ):
            raise ForbiddenError("комментирование недоступно из-за блокировки", code=rules.BLOCKED)

    async def post(self, actor: Actor, collection_id: UUID, body: str, key: str) -> CommentDto:
        """Опубликовать комментарий (повтор по ``Idempotency-Key`` - тот же)."""
        await self._ensure_not_muted(actor)
        cfg = await current_config(self._config)
        fingerprint = request_fingerprint("comment.post", collection_id, {"body": body})
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                ref = await uow.collections.get(collection_id)
                if ref is None or not ref.published:
                    raise NotFoundError("сборник не найден", code=rules.COLLECTION_NOT_FOUND)
                await self._ensure_not_blocked(uow, actor.user_id, ref.author_id)
                text = self._check_text(body, cfg)
                hits = await self._limiter.hit(
                    f"comments:{actor.user_id}", window_s=COMMENTS_WINDOW_S
                )
                if hits > cfg.comments.per_minute:
                    raise RateLimitedError(
                        "слишком много комментариев",
                        code=rules.COMMENTS_RATE_LIMIT,
                        details={"retry_after": COMMENTS_WINDOW_S},
                    )
                now = self._clock.now()
                comment = Comment(
                    id=self._new_id(),
                    collection_id=collection_id,
                    author_id=actor.user_id,
                    body=text,
                    created_at=now,
                )
                await uow.comments.add(comment)
                nick = (await uow.nicknames.get([actor.user_id])).get(
                    actor.user_id, FALLBACK_NICKNAME
                )
                # У кураторского сборника автора нет: схема требует uuid, уведомлять некого.
                owner = ref.author_id or actor.user_id
                # Защита: заблокированный сюда не доходит, но владельца ему не уведомляем.
                notify = (
                    ref.author_id is not None
                    and ref.author_id != actor.user_id
                    and not await uow.blocks.is_blocked(ref.author_id, actor.user_id)
                )
                uow.emit(
                    CommentPosted(
                        aggregate_id=str(comment.id),
                        at=now,
                        author_id=actor.user_id,
                        author_nickname=nick,
                        collection_author_id=owner,
                        collection_id=collection_id,
                        collection_title=ref.title[:TITLE_MAX],
                        comment_id=comment.id,
                        notify_owner=notify,
                    )
                )
                return self._dto(comment, nick, actor, ref.author_id, cfg).to_json()

            data, _ = await run_idempotent(
                uow.idempotency, actor.user_id, key, fingerprint, perform, status_code=201
            )
            await uow.commit()
        return CommentDto.from_json(data)

    async def edit(self, actor: Actor, comment_id: UUID, body: str) -> CommentDto:
        """Изменить свой комментарий в окне правки."""
        await self._ensure_not_muted(actor)
        cfg = await current_config(self._config)
        async with self._uow() as uow:
            comment = await uow.comments.get(comment_id, for_update=True)
            if comment is None or comment.is_deleted or comment.author_id != actor.user_id:
                raise NotFoundError("комментарий не найден", code=rules.COMMENT_NOT_FOUND)
            ref = await uow.collections.get(comment.collection_id)
            if ref is None or not ref.published:
                raise NotFoundError("сборник не найден", code=rules.COLLECTION_NOT_FOUND)
            await self._ensure_not_blocked(uow, actor.user_id, ref.author_id)
            now = self._clock.now()
            if not rules.can_edit(comment, actor.user_id, now, cfg.comments.edit_window_s):
                raise ConflictError("время правки истекло", code=rules.COMMENT_EDIT_EXPIRED)
            text = self._check_text(body, cfg)
            await uow.comments.set_body(comment_id, text, now)
            nick = (await uow.nicknames.get([actor.user_id])).get(actor.user_id, FALLBACK_NICKNAME)
            edited = Comment(
                id=comment.id,
                collection_id=comment.collection_id,
                author_id=comment.author_id,
                body=text,
                created_at=comment.created_at,
                edited_at=now,
            )
            await uow.commit()
        return self._dto(edited, nick, actor, ref.author_id, cfg)

    async def delete(self, actor: Actor, comment_id: UUID) -> None:
        """Удалить комментарий (автор, автор сборника, модератор); повтор - без эффекта."""
        async with self._uow() as uow:
            comment = await uow.comments.get(comment_id)
            if comment is None:
                raise NotFoundError("комментарий не найден", code=rules.COMMENT_NOT_FOUND)
            ref = await uow.collections.get(comment.collection_id)
            role = rules.delete_role(
                comment, actor.user_id, actor.perms, ref.author_id if ref else None
            )
            if role is None:
                raise NotFoundError("комментарий не найден", code=rules.COMMENT_NOT_FOUND)
            if comment.is_deleted:
                return
            now = self._clock.now()
            await uow.comments.mark_deleted(comment_id, at=now, by=actor.user_id, role=role)
            uow.emit(
                CommentDeleted(
                    aggregate_id=str(comment_id),
                    at=now,
                    by_role=role,
                    collection_id=comment.collection_id,
                    comment_id=comment_id,
                    deleted_by=actor.user_id,
                )
            )
            await uow.commit()

    async def page(
        self, viewer: Actor | None, collection_id: UUID, cursor: str | None, limit: int
    ) -> CommentPageDto:
        """Страница живых комментариев, новые первыми."""
        after = _decode(cursor) if cursor else None
        size = clamp_limit(limit)
        async with self._uow() as uow:
            ref = await uow.collections.get(collection_id)
            if ref is None or not ref.published:
                raise NotFoundError("сборник не найден", code=rules.COLLECTION_NOT_FOUND)
            rows = await uow.comments.page(collection_id, after=after, limit=size + 1)
            total = await uow.comments.count(collection_id)
            has_more = len(rows) > size
            rows = rows[:size]
            nicks = await uow.nicknames.get(sorted({r.author_id for r in rows}))
        cfg = await current_config(self._config) if viewer is not None else None
        items = [
            self._dto(r, nicks.get(r.author_id, FALLBACK_NICKNAME), viewer, ref.author_id, cfg)
            for r in rows
        ]
        next_cursor = (
            encode_cursor({"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)})
            if has_more
            else None
        )
        return CommentPageDto(items=items, next_cursor=next_cursor, total=total)

    def _dto(
        self,
        comment: Comment,
        nickname: str,
        viewer: Actor | None,
        collection_author: UUID | None,
        cfg: SocialConfig | None,
    ) -> CommentDto:
        can_edit = can_delete = False
        if viewer is not None and cfg is not None:
            can_edit = rules.can_edit(
                comment, viewer.user_id, self._clock.now(), cfg.comments.edit_window_s
            )
            can_delete = (
                rules.delete_role(comment, viewer.user_id, viewer.perms, collection_author)
                is not None
            )
        return CommentDto(
            id=comment.id,
            collection_id=comment.collection_id,
            author_id=comment.author_id,
            author_nickname=nickname,
            body=comment.body,
            created_at=comment.created_at,
            edited_at=comment.edited_at,
            can_edit=can_edit,
            can_delete=can_delete,
        )


def _decode(cursor: str) -> tuple[datetime, UUID]:
    data = decode_cursor(cursor)
    try:
        return datetime.fromisoformat(data["t"]), UUID(data["i"])
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidInputError("неверный курсор", code="pagination.bad_cursor") from exc


__all__ = ["Actor", "CommentDto", "CommentPageDto", "CommentService"]
