"""JSON-представления сессии по контракту §2.6 (снимок, ход, итог, ассет уровня).

Ответ хода сохраняется в ``idempotency_keys`` как есть, поэтому use-cases собирают JSON
сами, а API только проверяет его схемой ответа. Античит (D11): ``media_item_id``, название
и персоны попадают в ответ только в ``reveal`` завершённого (не ``void``) раунда;
``asset_set_id`` не попадает никогда.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from game_service.application.ports import Json
from game_service.application.rules import ModeRules
from game_service.domain.projection import CatalogItem
from game_service.domain.session import GameSession, Round
from game_service.domain.share import share_text
from gv_core.clock import Clock
from gv_game_kernel import AssetPort, ClueView, RoundResult


def subject_of(session_id: UUID) -> str:
    """Субъект выдачи ссылок media-pipeline (ключ лимита, ADR 0012)."""
    return f"sess:{session_id}"


def iso(moment: datetime) -> str:
    """RFC 3339 в UTC."""
    return moment.isoformat()


def clue_json(view: ClueView) -> Json:
    """Ответ ``POST .../clue``."""
    return {
        "level": view.level,
        "kind": view.kind,
        "url": view.asset.url,
        "expires_at": iso(view.asset.expires_at),
        "mime": view.asset.mime,
        "meta": dict(view.meta),
    }


class RevealLinkCache:
    """Ссылки ревила в памяти процесса до истечения минус запас.

    Снимок сессии отдаёт ревил каждого завершённого раунда; без кэша каждый ``GET`` и
    каждый ход ходили бы в media-pipeline за ссылками всех прошлых раундов.
    """

    def __init__(self, clock: Clock, *, margin_s: float, max_size: int) -> None:
        self._clock = clock
        self._margin = timedelta(seconds=margin_s)
        self._max = max_size
        self._items: OrderedDict[tuple[UUID, int], ClueView] = OrderedDict()

    def get(self, session_id: UUID, index: int) -> ClueView | None:
        """Действующая ссылка или ``None``."""
        key = (session_id, index)
        view = self._items.get(key)
        if view is None:
            return None
        if view.asset.expires_at - self._margin <= self._clock.now():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return view

    def put(self, session_id: UUID, index: int, view: ClueView) -> None:
        """Запомнить ссылку (вытесняется самая старая)."""
        self._items[(session_id, index)] = view
        self._items.move_to_end((session_id, index))
        while len(self._items) > self._max:
            self._items.popitem(last=False)


@dataclass(frozen=True, slots=True)
class ShareSettings:
    """Параметры текста «Поделиться»."""

    title: str
    url: str


class SessionPresenter:
    """Сборка JSON сессии.

    Args:
        assets: Порт ссылок (для ``final_clue`` ревила).
        cache: Кэш ссылок ревила.
        share: Параметры шэра.
    """

    def __init__(self, assets: AssetPort, cache: RevealLinkCache, share: ShareSettings) -> None:
        self._assets = assets
        self._cache = cache
        self._share = share

    async def final_clue(self, session: GameSession, r: Round, rules: ModeRules) -> ClueView | None:
        """Ссылка ревила завершённого раунда (из кэша или media-pipeline)."""
        if r.asset is None or r.is_void or not r.finished:
            return None
        cached = self._cache.get(session.id, r.index)
        if cached is not None:
            return cached
        view = await rules.final_view(r.asset, assets=self._assets, subject=subject_of(session.id))
        self._cache.put(session.id, r.index, view)
        return view

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
            "final_clue": {
                "kind": view.kind,
                "url": view.asset.url,
                "expires_at": iso(view.asset.expires_at),
                "mime": view.asset.mime,
            },
        }

    def _status(self, session: GameSession, r: Round) -> str:
        if r.is_void:
            return "void"
        if r.finished:
            return "finished"
        return "active" if session.is_active and r.index == session.current_index else "pending"

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
        }

    async def snapshot(
        self, session: GameSession, rules: ModeRules, items: Mapping[UUID, CatalogItem]
    ) -> Json:
        """Снимок сессии."""
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
            "rounds": [await self.round_json(session, r, rules, items) for r in session.rounds],
        }

    async def result(
        self,
        session: GameSession,
        rules: ModeRules,
        items: Mapping[UUID, CatalogItem],
        daily_stats: Mapping[int, tuple[int, int]] | None,
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
        }


def _pct(played: int, exact: int) -> float:
    return round(100.0 * exact / played, 1) if played else 0.0
