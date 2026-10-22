"""Запросы публичного API: каталог режимов, итог сессии, статус плановых наборов
субъекта, персоны марафона (D14), личные рекорды (D11); этап 3b — режимы сборника и
играбельность единиц черновика (ADR 0028), темы сетки для админки (хвост 3a)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from game_service.application.catalog import ModeCatalog
from game_service.application.commands.play import Player, items_for
from game_service.application.ports import Json, RateLimiter, UnitOfWorkFactory
from game_service.application.rules import ModeRules
from game_service.application.views import RecordView, SessionPresenter
from game_service.domain.errors import (
    CollectionNotFoundError,
    CollectionNotSupportedError,
    SessionNotFinishedError,
    SessionNotFoundError,
    SubjectsNotSupportedError,
)
from game_service.domain.session import SessionStatus
from gv_core.clock import Clock
from gv_game_kernel import OnlineSessionSpec, PlayableFor, SubjectQuery


def online_json(rules: ModeRules, session: OnlineSessionSpec) -> Json:
    """Правила комнаты онлайн-режима для хаба и экрана создания лобби (ADR 0032).

    Только то, что видно игроку до матча: вид, места, раунды, окна уровней, что может
    выбрать хост и ключи эмоций/фраз. План раундов и ключи ответа здесь не появляются.
    """
    lobby = session.lobby
    return {
        "kind": session.kind,
        "players": {"min": session.players.min, "max": session.players.max},
        "rounds": session.rounds,
        "level_windows_s": list(session.level_windows_s),
        "contest_type": session.contest.type,
        "ranked": session.ranked,
        "ladder": session.ladder,
        "max_collections": rules.max_collections if rules.engine.uses_collection else None,
        "lobby": lobby.model_dump(mode="json", exclude_none=True) if lobby is not None else None,
        "chat": session.chat.model_dump(mode="json"),
    }


def mode_json(rules: ModeRules) -> Json:
    """Режим для ``GET /v1/modes``: подача выбирается по данным (D12), не по ключу.

    У шаблона сборника (``kind=collection``) — ещё ``round_options`` и ``min_items`` из
    конфига (без учёта конкретного сборника); у остальных они ``None``. У онлайн-режима
    (``session=online``) — блок ``online``; пресетов и выбора числа раундов у него нет
    (план раундов составляет match с game без них), ``allow_guest`` — всегда ``false``
    (онлайн только с аккаунтом, ADR 0036)."""
    session = rules.config.session
    online = session if isinstance(session, OnlineSessionSpec) else None
    template = rules.engine.uses_collection
    presets = rules.presets() if online is None else []
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
        "allow_guest": online is None and rules.engine.solo_session.allow_guest,
        "presets": [{"id": p.id, "title_key": p.title_key} for p in presets] or None,
        "answer_format": rules.answer_format,
        "presentation": rules.presentation,
        "start": {"subject": subject} if subject is not None else None,
        "round_options": rules.engine.round_options() if template and online is None else None,
        "min_items": rules.min_playable() if template else None,
        "online": online_json(rules, online) if online is not None else None,
    }


def _collection_mode_json(rules: ModeRules, playable: int, total: int) -> Json:
    """Режим для ``GET /v1/collections/{id}/modes``: варианты числа раундов — только те,
    что ``rounds_for`` ядра примет при текущем ``playable`` (иначе старт ответит 422)."""
    available = playable >= rules.min_playable()
    ceiling = min(playable, rules.config.session.rounds)
    options = [
        o for o in rules.engine.round_options() or [] if available and (o == "all" or o <= ceiling)
    ]
    return {
        "mode_key": rules.key,
        "title_key": rules.config.title_i18n,
        "clue_type": rules.clue_type,
        "allow_guest": rules.engine.solo_session.allow_guest,
        "playable": playable,
        "total": total,
        "available": available,
        "round_options": options,
    }


class PlayQueries:
    """``ListModes``, ``GetResult``, ``GetDailyStatus``, ``ListSubjects``, ``ListRecords``,
    ``CollectionModes``, ``Playability``, ``ListThemeSubjects`` (админка)."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        catalog: ModeCatalog,
        presenter: SessionPresenter,
        limiter: RateLimiter,
        *,
        subjects_per_min: int,
        playability_per_min: int = 30,
        candidate_limit: int = 5000,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._catalog = catalog
        self._presenter = presenter
        self._limiter = limiter
        self._subjects_per_min = subjects_per_min
        self._playability_per_min = playability_per_min
        self._candidate_limit = candidate_limit

    async def modes(
        self, domain: str | None, *, collection: bool = False, online: bool = False
    ) -> list[Json]:
        """Опубликованные режимы одного вида: по умолчанию — соло, играемые сами по себе, без
        шаблонов сборника (без ``collection_id`` их не начать, ADR 0028); при
        ``collection`` — только шаблоны сборника (конструктор, экран сессии по сборнику).

        При ``online`` — все онлайн-режимы (шаблоны дуэли, party и лобби, в том числе
        лобби по сборнику; ``collection`` не учитывается): соло-хаб их не видит (ADR 0032).
        """
        return [
            mode_json(r)
            for r in await self._catalog.published(online=online)
            if (domain is None or r.domain == domain)
            and (online or r.engine.uses_collection == collection)
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
        return await self._subject_rows(rules, query, q, limit)

    async def theme_subjects(self, mode_key: str, q: str | None, limit: int) -> list[Json]:
        """Темы периода сетки для админки: персоны с ролями и ``min_items`` из
        ``source.params.subject`` конфига (тот же поиск, что ``ListSubjects``).

        Raises:
            ModeNotFoundError: режима нет.
            SubjectsNotSupportedError: у режима нет темы-персоны (422).
        """
        _, rules = await self._catalog.latest(mode_key)
        query = rules.theme_query()
        if query is None:
            raise SubjectsNotSupportedError("у режима нет темы периода")
        return await self._subject_rows(rules, query, q, limit)

    async def _subject_rows(
        self, rules: ModeRules, query: SubjectQuery, q: str | None, limit: int
    ) -> list[Json]:
        async with self._uow() as uow:
            rows = await uow.projection.subjects(
                query, text_=q, limit=limit, also=rules.reveal_playable_for
            )
        return [
            {"subject_id": str(r.person_id), "name": r.name, "role": r.role, "items": r.items}
            for r in rows
        ]

    # ------------------------------------------------------------------ сборники
    async def collection_modes(self, collection_id: UUID) -> list[Json]:
        """Режимы сборника: опубликованные режимы с источником из сборника домена сборника и
        сколько его единиц играбельно в каждом (тот же адаптер, что при старте).

        Без названий и ``media_item_id`` (D6).

        Raises:
            CollectionNotFoundError: сборника нет, он скрыт или удалён.
        """
        async with self._uow() as uow:
            collection = await uow.collections.get(collection_id)
        if collection is None or not collection.is_playable:
            raise CollectionNotFoundError("сборник не найден")
        modes = [
            r
            for r in await self._catalog.published()
            if r.engine.uses_collection and r.domain == collection.domain
        ]
        async with self._uow() as uow:
            total = await uow.collections.total(collection_id)
            result = []
            for rules in modes:
                port = uow.collection_port(rules.reveal_playable_for)
                items = await port.items(
                    collection_id,
                    self._candidate_limit,
                    playable_for=PlayableFor(
                        rules.clue_type, rules.params_hash, scope=collection_id
                    ),
                )
                result.append(_collection_mode_json(rules, len({i.id for i in items}), total))
        return result

    async def playability(
        self, player: Player, mode_key: str, media_item_ids: Sequence[UUID]
    ) -> Json:
        """Какие единицы черновика играбельны в режиме сборника: активны в каталоге, домена
        режима и с каталожным набором ассетов (свои эмодзи автора появятся только после
        публикации). Без названий (D6).

        Raises:
            GameRateLimitedError: лимит ``GV_GAME_LIMITS__PLAYABILITY_PER_MIN``.
            ModeNotFoundError: режима нет.
            CollectionNotSupportedError: режим не играется по сборнику (422).
        """
        await self._limiter.hit(
            "playability", str(player.actor_id), limit=self._playability_per_min, window_s=60
        )
        _, rules = await self._catalog.latest(mode_key)
        if not rules.engine.uses_collection:
            raise CollectionNotSupportedError("режим не играется по сборнику")
        ids = list(dict.fromkeys(media_item_ids))
        async with self._uow() as uow:
            items = await uow.projection.get_items(ids)
            ready = set(await uow.projection.ready_assets(ids, rules.clue_type, rules.params_hash))
            reveal = rules.reveal_playable_for
            if reveal is not None:
                ready &= set(
                    await uow.projection.ready_assets(ids, reveal.clue_type, reveal.params_hash)
                )
        playable = [i for i in ids if i in ready and items[i].domain == rules.domain]
        return {"mode_key": rules.key, "playable_ids": [str(i) for i in playable]}

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
