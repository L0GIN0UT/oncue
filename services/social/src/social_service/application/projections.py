"""Проекции social из событий других сервисов (5c): сборники, матчи, оценки, удаление аккаунта.

Все обработчики идемпотентны: повтор события (inbox) не доходит сюда, а повтор того же факта
под другим ``event_id`` не меняет данные (версии, ``ON CONFLICT``, уникальность жалобы).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from gv_core.clock import Clock
from social_service.application.ports.cases import AvatarRef
from social_service.application.ports.discussion import DiscussionUowFactory
from social_service.application.reports import ReportService
from social_service.domain import discussion as rules
from social_service.domain.discussion import CollectionRef
from social_service.domain.discussion_events import CommentDeleted

TITLE_MAX = 120


class ProjectionService:
    """Применение входящих событий."""

    def __init__(self, uow_factory: DiscussionUowFactory, reports: ReportService, clock: Clock):
        self._uow = uow_factory
        self._reports = reports
        self._clock = clock

    async def collection_snapshot(self, payload: Mapping[str, Any]) -> None:
        """``CollectionPublished`` / ``CollectionUpdated``: полный снимок агрегата."""
        raw_author = payload.get("author_id")
        ref = CollectionRef(
            id=UUID(payload["collection_id"]),
            author_id=UUID(raw_author) if raw_author else None,
            # Названия в событиях collections пока нет; поле появится - подхватим без правок.
            title=str(payload.get("title") or "")[:TITLE_MAX],
            published=payload["status"] == "published",
            version=int(payload["version"]),
        )
        async with self._uow() as uow:
            await uow.collections.apply(ref)
            await uow.commit()

    async def collection_deleted(self, payload: Mapping[str, Any]) -> None:
        """``CollectionDeleted``: сборник больше не принимает комментарии и жалобы."""
        collection_id = UUID(payload["collection_id"])
        async with self._uow() as uow:
            known = await uow.collections.get(collection_id)
            await uow.collections.apply(
                CollectionRef(
                    id=collection_id,
                    author_id=known.author_id if known else None,
                    title=known.title if known else "",
                    published=False,
                    version=int(payload["version"]),
                )
            )
            await uow.commit()

    async def asset_moderated(self, payload: Mapping[str, Any]) -> None:
        """``media_pipeline.AssetModerated``: владелец и статус аватарки (для жалоб на неё).

        Другие назначения изображений (обложки) жалоб не порождают и не хранятся.
        """
        if payload["purpose"] != "avatar":
            return
        ref = AvatarRef(
            asset_id=UUID(payload["asset_id"]),
            owner_user_id=UUID(payload["owner_user_id"]),
            status=str(payload["status"]),
            version=int(payload["version"]),
        )
        async with self._uow() as uow:
            await uow.avatars.apply(ref)
            await uow.commit()

    async def match_finished(self, payload: Mapping[str, Any]) -> None:
        """``MatchFinished``: участники матча и счётчики порядочности (один раз на игрока)."""
        match_id = UUID(payload["match_id"])
        players = payload["players"]
        finished_at = datetime.fromisoformat(payload["finished_at"])
        # Матчей на одного нет (соло - в game): все участники событий match несоло.
        async with self._uow() as uow:
            fresh = set(
                await uow.matches.add_participants(
                    match_id,
                    [UUID(p["user_id"]) for p in players],
                    solo=False,
                    finished_at=finished_at,
                )
            )
            if not payload["aborted"]:
                for p in players:
                    user_id = UUID(p["user_id"])
                    if user_id not in fresh:
                        continue
                    await uow.matches.bump(user_id, "matches_total")
                    field = rules.counter_for_finish(p["finish"])
                    if field is not None:
                        await uow.matches.bump(user_id, field)
            await uow.commit()

    async def player_rated(self, payload: Mapping[str, Any]) -> None:
        """``PlayerRated``: похвала - счётчик; жалоба - обычная жалоба на игрока."""
        author, target = UUID(payload["from_id"]), UUID(payload["to_id"])
        if payload["kind"] == "praise":
            if author == target:
                return
            async with self._uow() as uow:
                await uow.matches.bump(target, "praises")
                await uow.commit()
            return
        await self._reports.file_from_rating(
            author, target, UUID(payload["match_id"]), payload.get("reason")
        )

    async def user_deleted(self, payload: Mapping[str, Any]) -> None:
        """``UserDeleted``: комментарии удаляются (``account_deleted``), тексты жалоб очищаются."""
        user_id = UUID(payload["user_id"])
        now = self._clock.now()
        async with self._uow() as uow:
            for comment in await uow.comments.anonymize_author(user_id, at=now):
                uow.emit(
                    CommentDeleted(
                        aggregate_id=str(comment.id),
                        at=now,
                        by_role=rules.ROLE_ACCOUNT_DELETED,
                        collection_id=comment.collection_id,
                        comment_id=comment.id,
                        deleted_by=None,
                    )
                )
            await uow.reports.anonymize_author(user_id)
            await uow.commit()
