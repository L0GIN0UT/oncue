"""Ladder-ы: черновик, публикация, откат, сиды и чтение для админки (ADR 0037)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_db.idempotency import request_fingerprint, run_idempotent
from rating_service.application.commands.leaderboards import STORE_ERRORS, Leaderboards
from rating_service.application.commands.seasons import SeasonLifecycle
from rating_service.application.common import (
    AdminContext,
    audit,
    card_json,
    version_json,
)
from rating_service.application.ports import Json, RatingUnitOfWork, UnitOfWorkFactory
from rating_service.domain.errors import LadderNotFoundError
from rating_service.domain.ladder import LadderConfig
from rating_service.domain.ladder_line import LadderLine, LadderVersion
from rating_service.domain.season import Season

log = structlog.get_logger(__name__)


class LadderCommands:
    """Мутации ladder-ов; чтение для админки — ниже."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        lifecycle: SeasonLifecycle,
        boards: Leaderboards,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._lifecycle = lifecycle
        self._boards = boards

    @staticmethod
    async def _line(uow: RatingUnitOfWork, key: str) -> LadderLine:
        line = await uow.ladders.get_line(key, for_update=True)
        if line is None:
            raise LadderNotFoundError()
        return line

    # ------------------------------------------------------------------ сиды
    async def bootstrap(self, raw: Mapping[str, Any], *, actor_id: UUID, reason: str) -> bool:
        """Создать ladder из сида с опубликованной версией 1, если его ещё нет.

        Существующий ladder не трогается: дальше им владеет админ (черновик/публикация).

        Returns:
            ``True`` — ladder создан сейчас.

        Raises:
            LadderInvalidError: сид не проходит проверку.
        """
        config = LadderConfig.parse(raw)
        started: Season | None = None
        async with self._uow() as uow:
            if await uow.ladders.get_line(config.key, for_update=True) is not None:
                return False
            line = LadderLine.create(
                config, actor_id=actor_id, reason=reason, now=self._clock.now()
            )
            await uow.ladders.save_line(line)
            uow.track(line)
            ctx = AdminContext(actor_id, reason, f"bootstrap:{config.key}")
            await audit(uow, ctx, "ladder.bootstrap", config.key, self._clock.now(), version=1)
            published = await uow.ladders.published(config.key)
            assert published is not None  # noqa: S101 — только что опубликована
            started = await self._lifecycle.start(uow, published)
            await uow.commit()
        await self._refresh(started)
        return True

    # ------------------------------------------------------------------ админ
    async def save_draft(self, ladder: str, raw: Mapping[str, Any], *, ctx: AdminContext) -> Json:
        """Сохранить черновик (заменяет текущий) после проверки конфига.

        Raises:
            LadderInvalidError: конфиг не проходит проверку.
            LadderNotFoundError: нет ladder-а.
        """
        config = LadderConfig.parse(raw)
        fingerprint = request_fingerprint(
            "ladder.save_draft", ladder, {"config": config.to_dict(), "reason": ctx.reason}
        )
        async with self._uow() as uow:

            async def perform() -> Json:
                line = await self._line(uow, ladder)
                now = self._clock.now()
                draft = line.save_draft(config, actor_id=ctx.actor_id, reason=ctx.reason, now=now)
                await uow.ladders.save_line(line)
                await audit(uow, ctx, "ladder.save_draft", ladder, now, version=draft.version)
                return version_json(draft)

            response, _ = await run_idempotent(
                uow.idempotency, ctx.actor_id, ctx.idempotency_key, fingerprint, perform
            )
            await uow.commit()
        return response

    async def publish(self, ladder: str, *, version: int, ctx: AdminContext) -> Json:
        """Опубликовать черновик: версия действует для новых матчей, уходит ``LadderPublished``.

        Raises:
            LadderNotFoundError: нет ladder-а.
            VersionNotFoundError: нет версии.
            VersionNotDraftError: версия не черновик.
        """
        fingerprint = request_fingerprint(
            "ladder.publish", ladder, {"version": version, "reason": ctx.reason}
        )
        return await self._publishing(
            ladder,
            "ladder.publish",
            fingerprint,
            ctx,
            lambda line, now: line.publish(version, now=now),
        )

    async def rollback(self, ladder: str, *, to_version: int, ctx: AdminContext) -> Json:
        """Откат: копия ранее опубликованной версии публикуется новой версией.

        Raises:
            LadderNotFoundError: нет ladder-а.
            VersionNotFoundError: нет версии.
            VersionNotPublishedError: версия никогда не публиковалась.
        """
        fingerprint = request_fingerprint(
            "ladder.rollback", ladder, {"to": to_version, "reason": ctx.reason}
        )
        return await self._publishing(
            ladder,
            "ladder.rollback",
            fingerprint,
            ctx,
            lambda line, now: line.rollback(
                to_version, actor_id=ctx.actor_id, reason=ctx.reason, now=now
            ),
        )

    async def _publishing(
        self,
        ladder: str,
        action: str,
        fingerprint: str,
        ctx: AdminContext,
        change: Callable[[LadderLine, datetime], LadderVersion],
    ) -> Json:
        started: Season | None = None
        async with self._uow() as uow:

            async def perform() -> Json:
                nonlocal started
                line = await self._line(uow, ladder)
                now = self._clock.now()
                version = change(line, now)
                await uow.ladders.save_line(line)
                uow.track(line)
                await audit(uow, ctx, action, ladder, now, version=version.version)
                # Первая публикация ladder-а открывает первый сезон.
                if (
                    not await uow.seasons.open_seasons(ladder)
                    and await uow.seasons.latest_finished(ladder) is None
                ):
                    published = await uow.ladders.published(ladder)
                    assert published is not None  # noqa: S101 — только что опубликована
                    started = await self._lifecycle.start(uow, published)
                return version_json(version)

            response, _ = await run_idempotent(
                uow.idempotency, ctx.actor_id, ctx.idempotency_key, fingerprint, perform
            )
            await uow.commit()
        await self._refresh(started)
        return response

    async def _refresh(self, season: Season | None) -> None:
        if season is None:
            return
        try:
            await self._boards.rebuild(season.ladder, season.season_id)
        except STORE_ERRORS:
            log.warning("rating.season_board_failed", ladder=season.ladder, exc_info=True)


class LadderQueries:
    """Чтение ladder-ов для админки."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def list(self) -> Json:
        """Ladder-ы с действующей и черновой версиями и текущим сезоном."""
        async with self._uow() as uow:
            items: list[Json] = []
            for s in await uow.ladders.summaries():
                season = await uow.seasons.active(s.key)
                items.append(
                    {
                        "key": s.key,
                        "published_version": s.published_version,
                        "draft_version": s.draft_version,
                        "season_id": str(season.season_id) if season else None,
                    }
                )
        return {"items": items}

    async def versions(self, ladder: str) -> Json:
        """Версии ladder-а, новые первыми."""
        async with self._uow() as uow:
            line = await uow.ladders.get_line(ladder)
            if line is None:
                raise LadderNotFoundError()
            ordered = sorted(line.versions, key=lambda v: v.version, reverse=True)
        return {"items": [card_json(v) for v in ordered]}

    async def version(self, ladder: str, version: int) -> Json:
        """Версия с телом конфига."""
        async with self._uow() as uow:
            line = await uow.ladders.get_line(ladder)
            if line is None:
                raise LadderNotFoundError()
            return version_json(line.get(version))
