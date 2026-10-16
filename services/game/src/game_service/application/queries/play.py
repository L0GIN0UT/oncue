"""Запросы публичного API: каталог режимов, итог сессии, статус плановых наборов
субъекта, персоны марафона (D14), личные рекорды (D11)."""

from __future__ import annotations

from uuid import UUID

from game_service.application.catalog import ModeCatalog
from game_service.application.commands.play import Player, items_for
from game_service.application.ports import Json, RateLimiter, UnitOfWorkFactory
from game_service.application.rules import ModeRules
from game_service.application.views import RecordView, SessionPresenter
from game_service.domain.errors import (
    SessionNotFinishedError,
    SessionNotFoundError,
    SubjectsNotSupportedError,
)
from game_service.domain.session import SessionStatus
from gv_core.clock import Clock


def mode_json(rules: ModeRules) -> Json:
    """Режим для ``GET /v1/modes``: подача выбирается по данным (D12), не по ключу."""
    presets = rules.presets()
    query = rules.subject_query()
    limit = rules.max_subjects()
    subject = (
        {"roles": sorted(query.roles), "max": limit}
        if query is not None and limit is not None
        else None
    )
    return {
        "key": rules.key,
        "version": rules.version,
        "domain": rules.domain,
        "title_key": rules.config.title_i18n,
        "source_type": rules.source_type,
        "clue_type": rules.clue_type,
        "rounds": rules.rounds,
        "levels": [{"index": lvl.index, "hint": dict(lvl.hint)} for lvl in rules.levels],
        "allow_guest": rules.config.session.allow_guest,
        "presets": [{"id": p.id, "title_key": p.title_key} for p in presets] or None,
        "answer_format": rules.answer_format,
        "presentation": rules.presentation,
        "start": {"subject": subject} if subject is not None else None,
    }


class PlayQueries:
    """``ListModes``, ``GetResult``, ``GetDailyStatus``, ``ListSubjects``, ``ListRecords``."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        catalog: ModeCatalog,
        presenter: SessionPresenter,
        limiter: RateLimiter,
        *,
        subjects_per_min: int,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._catalog = catalog
        self._presenter = presenter
        self._limiter = limiter
        self._subjects_per_min = subjects_per_min

    async def modes(self, domain: str | None) -> list[Json]:
        """Опубликованные режимы."""
        return [
            mode_json(r)
            for r in await self._catalog.published()
            if domain is None or r.domain == domain
        ]

    async def result(self, player: Player, session_id: UUID) -> Json:
        """Итог завершённой своей сессии.

        Raises:
            SessionNotFoundError: сессии нет или она чужая.
            SessionNotFinishedError: сессия не завершена.
        """
        record: RecordView | None = None
        async with self._uow() as uow:
            session = await uow.sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError("сессия не найдена")
            session.require_owner(player.ref)
            if session.status is not SessionStatus.FINISHED:
                raise SessionNotFinishedError("сессия ещё не завершена")
            rules = await self._catalog.rules(session.mode_key, session.mode_version)
            items = await items_for(uow, session)
            stats = (
                await uow.stats.get(session.mode_key, session.day)
                if session.day is not None and not session.progressive
                else None
            )
            user_id = session.player.user_id
            if rules.records_enabled and user_id is not None:
                found = await uow.records.get(user_id, session.mode_key)
                if found is not None:
                    record = RecordView(
                        best=found.best,
                        is_new=found.session_id == session.id,
                        percentile=await uow.records.percentile(session.mode_key, found.best),
                    )
        return await self._presenter.result(session, rules, items, stats, record)

    async def daily(self, player: Player, domain: str | None) -> list[Json]:
        """Плановые наборы субъекта (дейлик, неделя сетки): статус, очки, смена периода."""
        now = self._clock.now()
        result = []
        for rules in await self._catalog.published():
            if rules.schedule is None or (domain is not None and rules.domain != domain):
                continue
            day = rules.period_start(now)
            async with self._uow() as uow:
                session = await uow.sessions.find_daily(rules.key, day, player.ref)
            if session is None:
                status, score, max_score = "not_started", None, rules.round_max * rules.rounds
            else:
                status = "in_progress" if session.is_active else "finished"
                score, max_score = session.score, session.max_score
            result.append(
                {
                    "mode_key": rules.key,
                    "day": day.isoformat(),
                    "status": status,
                    "session_id": str(session.id) if session else None,
                    "score": score,
                    "max_score": max_score,
                    "next_reset_at": rules.next_reset_at(now).isoformat(),
                }
            )
        return result

    async def subjects(
        self, player: Player, mode_key: str, q: str | None, limit: int
    ) -> list[Json]:
        """Персоны для марафона: имя содержит ``q``, у персоны ≥ ``min_items`` играбельных.

        Raises:
            ModeNotFoundError: режима нет.
            SubjectsNotSupportedError: режим не выбирает персон (422).
            GameRateLimitedError: лимит ``GV_GAME_LIMITS__SUBJECTS_PER_MIN``.
        """
        await self._limiter.hit(
            "subjects", str(player.actor_id), limit=self._subjects_per_min, window_s=60
        )
        _, rules = await self._catalog.latest(mode_key)
        query = rules.subject_query()
        if query is None:
            raise SubjectsNotSupportedError("режим не выбирает персон")
        async with self._uow() as uow:
            rows = await uow.projection.subjects(
                query, text_=q, limit=limit, also=rules.reveal_playable_for
            )
        return [
            {"subject_id": str(r.person_id), "name": r.name, "role": r.role, "items": r.items}
            for r in rows
        ]

    async def records(self, player: Player, mode_key: str | None) -> list[Json]:
        """Личные рекорды пользователя (гость — пусто; чужих рекордов не видно)."""
        user_id = player.ref.user_id
        if user_id is None:
            return []
        async with self._uow() as uow:
            found = await uow.records.for_user(user_id, mode_key)
            return [
                {
                    "mode_key": r.mode_key,
                    "best": r.best,
                    "percentile": await uow.records.percentile(r.mode_key, r.best),
                    "achieved_at": r.achieved_at.isoformat(),
                }
                for r in found
            ]
