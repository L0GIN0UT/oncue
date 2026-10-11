"""Запросы публичного API: каталог режимов, итог сессии, статус дейликов субъекта."""

from __future__ import annotations

from uuid import UUID

from game_service.application.catalog import ModeCatalog
from game_service.application.commands.play import Player, items_for
from game_service.application.ports import Json, UnitOfWorkFactory
from game_service.application.rules import ModeRules
from game_service.application.views import SessionPresenter
from game_service.domain.daily import day_in_zone, next_reset_at
from game_service.domain.errors import SessionNotFinishedError, SessionNotFoundError
from game_service.domain.session import SessionStatus
from gv_core.clock import Clock


def mode_json(rules: ModeRules) -> Json:
    """Режим для ``GET /v1/modes``."""
    presets = rules.presets()
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
    }


class PlayQueries:
    """``ListModes``, ``GetResult``, ``GetDailyStatus``."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        catalog: ModeCatalog,
        presenter: SessionPresenter,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._catalog = catalog
        self._presenter = presenter

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
        async with self._uow() as uow:
            session = await uow.sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError("сессия не найдена")
            session.require_owner(player.ref)
            if session.status is not SessionStatus.FINISHED:
                raise SessionNotFinishedError("сессия ещё не завершена")
            items = await items_for(uow, session)
            stats = await uow.stats.get(session.mode_key, session.day) if session.day else None
        rules = await self._catalog.rules(session.mode_key, session.mode_version)
        return await self._presenter.result(session, rules, items, stats)

    async def daily(self, player: Player, domain: str | None) -> list[Json]:
        """Дейлики текущего субъекта: статус, очки, следующая смена дня."""
        now = self._clock.now()
        result = []
        for rules in await self._catalog.published():
            schedule = rules.schedule
            if schedule is None or (domain is not None and rules.domain != domain):
                continue
            day = day_in_zone(now, schedule.timezone)
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
                    "next_reset_at": next_reset_at(now, schedule.timezone).isoformat(),
                }
            )
        return result
