"""JSON-представления сессии по контракту (снимок, ход, итог, ассет уровня).

Ответ хода сохраняется в ``idempotency_keys`` как есть, поэтому use-cases собирают JSON
сами, а API только проверяет его схемой ответа. Античит (D11, план этапа 3a §11 п. 1):

* ``media_item_id``, название и персоны загаданной единицы — только в ``reveal``
  завершённого (не ``void``) раунда; ``asset_set_id`` — никогда;
* варианты ответа — непрозрачные ``option_id``; название за вариантом — только если его
  показывает источник (сетка), у «больше/меньше» вариант — подпись;
* метрика сравнения — только у открыто показанной (опорной) единицы и в ревиле;
* план сессии (невыданные раунды) в снимок не попадает: вместо них — пустые раунды
  ``pending`` без состава.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import structlog

from game_service.application.ports import Json
from game_service.application.rules import ModeRules
from game_service.domain.projection import CatalogItem
from game_service.domain.session import GameSession, Round
from game_service.domain.share import share_text
from gv_core.clock import Clock
from gv_core.errors import GoneError
from gv_game_kernel import AssetPort, AssetRef, ClueView, ModeEngine, RoundOutcome, RoundResult

log = structlog.get_logger(__name__)

REVEAL = "reveal"
"""Тег кэша: ассет ревила раунда."""


def subject_of(session_id: UUID) -> str:
    """Субъект выдачи ссылок media-pipeline (ключ лимита, ADR 0012)."""
    return f"sess:{session_id}"


def iso(moment: datetime) -> str:
    """RFC 3339 в UTC."""
    return moment.isoformat()


def clue_json(view: ClueView) -> Json:
    """Ответ ``POST .../clue`` и ``POST .../final-clue``."""
    return {
        "level": view.level,
        "kind": view.kind,
        "url": view.asset.url,
        "expires_at": iso(view.asset.expires_at),
        "mime": view.asset.mime,
        "meta": dict(view.meta),
    }


def asset_json(view: ClueView) -> Json:
    """Ссылка на полный ассет (``final_clue`` ревила, ``shown[].clue``)."""
    return {
        "kind": view.kind,
        "url": view.asset.url,
        "expires_at": iso(view.asset.expires_at),
        "mime": view.asset.mime,
    }


def round_items(session: GameSession) -> list[UUID]:
    """Единицы, нужные снимку и итогу: цели завершённых раундов (ревил), показанные
    единицы и единицы вариантов выданных раундов. Цель идущего раунда не загружается."""
    ids: list[UUID] = []
    for r in session.rounds:
        if r.finished and not r.is_void:
            ids.append(r.media_item_id)
        ids.extend(s.media_item_id for s in r.shown)
        ids.extend(o.media_item_id for o in r.options if o.media_item_id is not None)
    return list(dict.fromkeys(ids))


def outcomes_until(session: GameSession, index: int, *, inclusive: bool) -> list[RoundOutcome]:
    """Итоги завершённых раундов до ``index`` (и самого ``index``, если ``inclusive``) —
    история для правила завершения и серии ядра."""
    last = index + 1 if inclusive else index
    return [
        ModeEngine.outcome(r.state, elapsed_ms=r.elapsed_ms)
        for r in session.rounds[:last]
        if r.finished
    ]


class RevealLinkCache:
    """Ссылки ревила и подачи показанных единиц в памяти процесса до истечения минус запас.

    Снимок сессии отдаёт ревил каждого завершённого раунда; без кэша каждый ``GET`` и
    каждый ход ходили бы в media-pipeline за ссылками всех прошлых раундов.
    """

    def __init__(self, clock: Clock, *, margin_s: float, max_size: int) -> None:
        self._clock = clock
        self._margin = timedelta(seconds=margin_s)
        self._max = max_size
        self._items: OrderedDict[tuple[UUID, int, str], ClueView] = OrderedDict()

    def get(self, session_id: UUID, index: int, tag: str = REVEAL) -> ClueView | None:
        """Действующая ссылка или ``None``."""
        key = (session_id, index, tag)
        view = self._items.get(key)
        if view is None:
            return None
        if view.asset.expires_at - self._margin <= self._clock.now():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return view

    def put(self, session_id: UUID, index: int, view: ClueView, tag: str = REVEAL) -> None:
        """Запомнить ссылку (вытесняется самая старая)."""
        key = (session_id, index, tag)
        self._items[key] = view
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)


@dataclass(frozen=True, slots=True)
class ShareSettings:
    """Параметры текста «Поделиться»."""

    title: str
    url: str


@dataclass(frozen=True, slots=True)
class RecordView:
    """Рекорд для итога сессии (только пользователь и ``features.records``)."""

    best: int
    is_new: bool
    percentile: float | None


class SessionPresenter:
    """Сборка JSON сессии.

    Args:
        assets: Порт ссылок (ревил и подача показанных единиц).
        cache: Кэш ссылок.
        share: Параметры шэра.
    """

    def __init__(self, assets: AssetPort, cache: RevealLinkCache, share: ShareSettings) -> None:
        self._assets = assets
        self._cache = cache
        self._share = share

    # ------------------------------------------------------------------ ссылки
    async def issue_final_clue(self, session: GameSession, r: Round, rules: ModeRules) -> ClueView:
        """Свежая ссылка ревила (мимо кэша) — ``POST .../final-clue`` (D13); кладётся в кэш.

        Raises:
            RoundNotFinishedError: раунд идёт.
            GoneError: набор загадки (или ревила другого типа) недоступен.
        """
        assert r.asset is not None  # noqa: S101 — вызывающий проверил, что раунд не void
        view = await rules.engine.final_clue(
            r.state,
            r.asset,
            assets=self._assets,
            subject=subject_of(session.id),
            reveal_asset=r.reveal_asset,
        )
        self._cache.put(session.id, r.index, view)
        return view

    async def final_clue(self, session: GameSession, r: Round, rules: ModeRules) -> ClueView | None:
        """Ссылка ревила завершённого раунда (из кэша или media-pipeline)."""
        if r.asset is None or r.is_void or not r.finished:
            return None
        cached = self._cache.get(session.id, r.index)
        if cached is not None:
            return cached
        try:
            return await self.issue_final_clue(session, r, rules)
        except GoneError:
            log.warning("game.reveal_gone", session_id=str(session.id), round=r.index)
            return None

    async def _shown_clue(
        self,
        session: GameSession,
        r: Round,
        item_id: UUID,
        asset: AssetRef | None,
        rules: ModeRules,
    ) -> Json | None:
        if asset is None:
            return None
        tag = f"shown:{item_id}"
        view = self._cache.get(session.id, r.index, tag)
        if view is None:
            try:
                view = await rules.shown_view(
                    asset, assets=self._assets, subject=subject_of(session.id)
                )
            except GoneError:
                return None
            self._cache.put(session.id, r.index, view, tag)
        return asset_json(view)

    # ------------------------------------------------------------------ раунд
    async def reveal(
        self, session: GameSession, r: Round, rules: ModeRules, item: CatalogItem | None
    ) -> Json | None:
        """Ревил раунда — только завершённого и не аннулированного."""
        if not r.finished or r.is_void or item is None:
            return None
        view = await self.final_clue(session, r, rules)
        if view is None:
            return None
        return {
            "media_item_id": str(r.media_item_id),
            "title": item.title,
            "year": item.year,
            "persons": [{"name": p.name, "role": p.role} for p in item.persons],
            "metric": rules.engine.metric_of(item.to_ref()),
            "final_clue": asset_json(view),
        }

    def _status(self, session: GameSession, r: Round) -> str:
        if r.is_void:
            return "void"
        if r.finished:
            return "finished"
        return "active" if session.is_active and r.index == session.current_index else "pending"

    async def _shown(
        self,
        session: GameSession,
        r: Round,
        rules: ModeRules,
        items: Mapping[UUID, CatalogItem],
    ) -> list[Json]:
        by_item = {o.media_item_id: o.id for o in r.options if o.media_item_id is not None}
        result = []
        for shown in r.shown:
            item = items.get(shown.media_item_id)
            if item is None:
                continue
            result.append(
                {
                    "option_id": by_item.get(shown.media_item_id),
                    "title": item.title,
                    "year": item.year,
                    "metric": rules.engine.metric_of(item.to_ref()),
                    "clue": await self._shown_clue(
                        session, r, shown.media_item_id, shown.asset, rules
                    ),
                }
            )
        return result

    @staticmethod
    def _options(r: Round, items: Mapping[UUID, CatalogItem]) -> list[Json]:
        return [
            {
                "option_id": o.id,
                "label_key": o.label_key,
                "title": (
                    items[o.media_item_id].title
                    if o.media_item_id is not None and o.media_item_id in items
                    else None
                ),
            }
            for o in r.options
        ]

    @staticmethod
    def _progress(session: GameSession, r: Round, rules: ModeRules) -> Json:
        history = outcomes_until(session, r.index, inclusive=r.finished)
        return {
            "lives_left": rules.engine.lives_left(history),
            "streak": rules.engine.streak(history),
            "stage": r.stage,
        }

    async def round_json(
        self,
        session: GameSession,
        r: Round,
        rules: ModeRules,
        items: Mapping[UUID, CatalogItem],
    ) -> Json:
        """Раунд снимка."""
        return {
            "index": r.index,
            "status": self._status(session, r),
            "level": r.state.level,
            "levels_count": r.levels_count,
            "guesses": [
                {
                    "kind": str(g.kind),
                    "display": g.display,
                    "result": str(g.verdict) if g.verdict is not None else None,
                }
                for g in r.state.guesses
            ],
            "points": r.points if r.finished else None,
            "reveal": await self.reveal(session, r, rules, items.get(r.media_item_id)),
            "shown": await self._shown(session, r, rules, items),
            "options": self._options(r, items),
            "progress": self._progress(session, r, rules),
        }

    @staticmethod
    def _pending(index: int, rules: ModeRules) -> Json:
        """Невыданный раунд плана: только номер, без состава (D4)."""
        return {
            "index": index,
            "status": "pending",
            "level": 1,
            "levels_count": rules.levels_count,
            "guesses": [],
            "points": None,
            "reveal": None,
            "shown": [],
            "options": [],
            "progress": None,
        }

    @staticmethod
    def pending_count(session: GameSession, rules: ModeRules) -> int:
        """Сколько раундов ещё впереди: план, а у сетки — до конца всех туров."""
        if not session.is_active:
            return 0
        planned = len(session.plan)
        if rules.is_progressive:
            planned = max(planned, rules.rounds - len(session.rounds))
        return planned

    async def snapshot(
        self, session: GameSession, rules: ModeRules, items: Mapping[UUID, CatalogItem]
    ) -> Json:
        """Снимок сессии (план — пустыми раундами ``pending``)."""
        rounds = [await self.round_json(session, r, rules, items) for r in session.rounds]
        start = len(rounds)
        rounds += [
            self._pending(start + i, rules) for i in range(self.pending_count(session, rules))
        ]
        return {
            "id": str(session.id),
            "version": session.version,
            "status": str(session.status),
            "mode": {
                "key": session.mode_key,
                "version": session.mode_version,
                "domain": session.domain,
                "clue_type": session.clue_type,
            },
            "day": session.day.isoformat() if session.day else None,
            "score": session.score,
            "max_score": session.max_score,
            "current_round": session.current_index,
            "rounds": rounds,
        }

    # ------------------------------------------------------------------ итог
    @staticmethod
    def _bracket(session: GameSession, items: Mapping[UUID, CatalogItem]) -> Json | None:
        if not session.progressive:
            return None
        picks = session.bracket_picks()
        if not picks:
            return None

        def title(item_id: UUID) -> str:
            item = items.get(item_id)
            return item.title if item is not None else ""

        return {
            "champion": title(picks[-1][1]),
            "picks": [
                {"stage": stage, "winner": title(w), "loser": title(lo)} for stage, w, lo in picks
            ],
        }

    async def result(
        self,
        session: GameSession,
        rules: ModeRules,
        items: Mapping[UUID, CatalogItem],
        daily_stats: Mapping[int, tuple[int, int]] | None,
        record: RecordView | None = None,
    ) -> Json:
        """Итог завершённой сессии."""
        rounds = [
            {
                "index": r.index,
                "outcome": str(r.state.outcome or RoundResult.MISS),
                "level": r.guessed_level,
                "points": r.points,
                "reveal": await self.reveal(session, r, rules, items.get(r.media_item_id)),
            }
            for r in session.rounds
        ]
        stats = None
        if daily_stats is not None:
            stats = [
                {
                    "index": r.index,
                    "exact_pct": _pct(*daily_stats.get(r.index, (0, 0))),
                }
                for r in session.rounds
                if not r.is_void
            ]
        text = share_text(
            title=self._share.title,
            mode_key=session.mode_key,
            day=session.day,
            score=session.score,
            max_score=session.max_score,
            rounds=[r.state for r in session.rounds],
            url=self._share.url,
        )
        return {
            "score": session.score,
            "max_score": session.max_score,
            "rounds": rounds,
            "share": {"text": text},
            "daily_stats": stats,
            "presentation": rules.presentation,
            "record": (
                {"best": record.best, "is_new": record.is_new, "percentile": record.percentile}
                if record is not None
                else None
            ),
            "bracket": self._bracket(session, items),
            "collection": (
                {"id": str(session.collection_id), "version": session.collection_version}
                if session.collection_id is not None
                else None
            ),
        }


def _pct(played: int, exact: int) -> float:
    return round(100.0 * exact / played, 1) if played else 0.0
