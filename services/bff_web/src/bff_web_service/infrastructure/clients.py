"""HTTP-клиенты game, catalog и identity по их OpenAPI-контрактам.

Схемы ниже — подмножество ``contracts/openapi/<service>.json``: только поля, нужные экранам,
лишние отбрасываются (источник может добавлять поля без релиза bff). Ответ, не прошедший
разбор, — нарушение контракта: блок гаснет (``UpstreamUnavailableError``), а не 500.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from bff_web_service.application.dto import (
    DailyRoundStat,
    DailyStatus,
    Domain,
    Item,
    Me,
    Mode,
    Person,
    Preset,
    ResultRound,
    Reveal,
    SessionResult,
    Share,
)
from bff_web_service.application.ports import UpstreamUnavailableError
from bff_web_service.infrastructure.http import UpstreamHttp

log = structlog.get_logger(__name__)


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


# ----------------------------------------------------------------------------- game
class _PresetIn(_In):
    id: str
    title_key: str


class _ModeIn(_In):
    key: str
    version: int
    domain: Domain
    title_key: str
    source_type: str
    clue_type: str
    rounds: int
    allow_guest: bool
    presets: list[_PresetIn] | None = None

    def dto(self) -> Mode:
        return Mode(
            key=self.key,
            version=self.version,
            domain=self.domain,
            title_key=self.title_key,
            source_type=self.source_type,
            clue_type=self.clue_type,
            rounds=self.rounds,
            allow_guest=self.allow_guest,
            presets=[Preset(p.id, p.title_key) for p in self.presets]
            if self.presets is not None
            else None,
        )


class _DailyIn(_In):
    mode_key: str
    day: date
    status: Literal["not_started", "in_progress", "finished"]
    max_score: int
    next_reset_at: datetime
    session_id: UUID | None = None
    score: int | None = None

    def dto(self) -> DailyStatus:
        return DailyStatus(
            mode_key=self.mode_key,
            day=self.day,
            status=self.status,
            max_score=self.max_score,
            next_reset_at=self.next_reset_at,
            session_id=self.session_id,
            score=self.score,
        )


class _RevealIn(_In):
    media_item_id: UUID


class _RoundIn(_In):
    index: int
    outcome: Literal["exact", "partial", "miss", "gave_up", "void"]
    level: int | None
    points: int
    reveal: _RevealIn | None


class _StatIn(_In):
    index: int
    exact_pct: float = Field(ge=0, le=100)


class _ShareIn(_In):
    text: str


class _ResultIn(_In):
    score: int
    max_score: int
    rounds: list[_RoundIn]
    share: _ShareIn
    daily_stats: list[_StatIn] | None = None

    def dto(self) -> SessionResult:
        return SessionResult(
            score=self.score,
            max_score=self.max_score,
            rounds=[
                ResultRound(
                    index=r.index,
                    outcome=r.outcome,
                    level=r.level,
                    points=r.points,
                    reveal=Reveal(r.reveal.media_item_id) if r.reveal else None,
                )
                for r in self.rounds
            ],
            share=Share(self.share.text),
            daily_stats=[DailyRoundStat(s.index, s.exact_pct) for s in self.daily_stats]
            if self.daily_stats is not None
            else None,
        )


_MODES = TypeAdapter(list[_ModeIn])
_DAILIES = TypeAdapter(list[_DailyIn])


def _parse[T](service: str, adapter: TypeAdapter[T], body: bytes) -> T:
    try:
        return adapter.validate_json(body)
    except ValidationError as exc:
        log.warning("bff.contract_violation", service=service, errors=exc.error_count())
        raise UpstreamUnavailableError(service, "contract_violation") from exc


class HttpGameClient:
    """Публичный API game (``/v1``).

    Список режимов публичный и одинаков для всех — кэшируется (``modes_ttl_s``); дейлики и
    итог личные и не кэшируются никогда.
    """

    def __init__(self, http: UpstreamHttp, *, modes_ttl_s: int, cache_prefix: str) -> None:
        self._http, self._ttl = http, modes_ttl_s
        self._modes_key = f"{cache_prefix}game:modes:v1"

    async def list_modes(self) -> list[Mode]:
        """Опубликованные режимы всех доменов."""
        body, cached = await self._http.get_public("/v1/modes", cache_key=self._modes_key)
        modes = _parse(self._http.service, _MODES, body)
        if not cached:
            await self._http.remember(self._modes_key, body, self._ttl)
        return [m.dto() for m in modes]

    async def daily(self, token: str, domain: Domain | None) -> list[DailyStatus]:
        """Дейлики субъекта токена."""
        params = {"domain": domain} if domain is not None else None
        body = await self._http.get("/v1/daily", token=token, params=params)
        return [d.dto() for d in _parse(self._http.service, _DAILIES, body)]

    async def session_result(self, token: str, session_id: UUID) -> SessionResult:
        """Итог своей завершённой сессии."""
        body = await self._http.get(f"/v1/sessions/{session_id}/result", token=token)
        return _parse(self._http.service, TypeAdapter(_ResultIn), body).dto()


# ----------------------------------------------------------------------------- catalog
class _PersonIn(_In):
    name: str
    role: str


class _ItemIn(_In):
    id: UUID
    domain: Domain
    kind: str
    title: str
    original_title: str | None = None
    year: int | None = None
    genres: list[str] = Field(default_factory=list)
    persons: list[_PersonIn] = Field(default_factory=list)

    def dto(self) -> Item:
        return Item(
            id=self.id,
            domain=self.domain,
            kind=self.kind,
            title=self.title,
            original_title=self.original_title,
            year=self.year,
            genres=list(self.genres),
            persons=[Person(p.name, p.role) for p in self.persons],
        )


_ITEM = TypeAdapter(_ItemIn)


class HttpCatalogClient:
    """Публичный API catalog: карточки единиц кэшируются (ключ без пользователя)."""

    def __init__(self, http: UpstreamHttp, *, item_ttl_s: int, cache_prefix: str) -> None:
        self._http, self._ttl, self._prefix = http, item_ttl_s, cache_prefix

    async def get_item(self, item_id: UUID) -> Item:
        """Публичная карточка единицы."""
        key = f"{self._prefix}catalog:item:v1:{item_id}"
        body, cached = await self._http.get_public(f"/v1/items/{item_id}", cache_key=key)
        item = _parse(self._http.service, _ITEM, body)
        if item.id != item_id:
            # Чужая карточка под нашим ключом закрепилась бы в кэше на весь TTL.
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
        if not cached:
            await self._http.remember(key, body, self._ttl)
        return item.dto()


# ----------------------------------------------------------------------------- identity
class _MeIn(_In):
    id: UUID
    display_name: str


_ME = TypeAdapter(_MeIn)


class HttpIdentityClient:
    """Публичный API identity: профиль субъекта токена (не кэшируется)."""

    def __init__(self, http: UpstreamHttp) -> None:
        self._http = http

    async def me(self, token: str) -> Me:
        """``GET /v1/me``; гость получает 403 ``auth.guest_forbidden``."""
        body = await self._http.get("/v1/me", token=token)
        me = _parse(self._http.service, _ME, body)
        return Me(id=me.id, display_name=me.display_name)
