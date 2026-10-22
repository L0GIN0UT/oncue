"""Порты application match: что use-cases получают снаружи (реализации — infrastructure).

Порты для фейков потока A (план §5 R5, R6):

* ``RoundPlanPort`` — план матча от game (``POST /internal/v1/round-plans``, D5);
* ``AssetPort`` ядра — ссылка уровня от media-pipeline (``assets.link``, D11);
* ``RealtimePublisher`` — публикация сообщения ``room.message.v1`` в канал Centrifugo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from match_service.domain.events import MatchStarted, PlayerRated
from match_service.domain.model import MatchState, RoundPlan
from match_service.domain.room import Room

# Any: JSON-тела ответов и сообщений (схемы — contracts/openapi/match.json, contracts/realtime).
type Json = dict[str, Any]


# ----------------------------------------------------------------------------- комнаты
@dataclass(frozen=True, slots=True)
class RoomCommand:
    """Команда комнаты в потоке ``{room:CODE}:cmd`` (D6).

    Attributes:
        op_id: Идентификатор команды клиента (повтор — тот же ответ).
        kind: ``join``, ``ready``, ``guess`` … (``RoomOwner.handle``).
        user_id: Кто отправил (для системных команд — затронутый пользователь).
        data: Параметры команды (уже проверены API).
    """

    op_id: str
    kind: str
    user_id: UUID
    data: Json

    @property
    def reply_key(self) -> str:
        """Ключ окна ответов: ``user_id:op_id`` (op_id разных игроков независимы)."""
        return f"{self.user_id}:{self.op_id}"


class RoomStore(Protocol):
    """Снимки комнат (Redis): чтение без владельца, создание, индексы."""

    async def load(self, code: str) -> Room | None:
        """Снимок комнаты или ``None`` (нет или истёк)."""
        ...

    async def create(self, room: Room) -> bool:
        """Сохранить новую комнату (``SET NX``) и передать её владельцам; ``False`` — код занят."""
        ...

    async def open_rooms(
        self, cursor: tuple[int, str] | None, limit: int
    ) -> tuple[list[Room], tuple[int, str] | None]:
        """Публичные лобби со свободными местами, новые первыми (keyset по времени и коду)."""
        ...

    async def active_rooms(
        self, cursor: str | None, limit: int
    ) -> tuple[list[tuple[Room, int | None]], str | None]:
        """Активные комнаты с эпохой владельца (админка)."""
        ...


class CommandBus(Protocol):
    """Доставка команды владельцу и ожидание ответа (D6)."""

    async def send(self, code: str, command: RoomCommand) -> None:
        """Положить команду в поток комнаты."""
        ...

    async def wait_reply(self, code: str, command: RoomCommand, timeout_ms: int) -> Json | None:
        """Ответ владельца или ``None`` по таймауту."""
        ...


class PresenceStore(Protocol):
    """Heartbeat игроков (``tick``) без владельца (D10)."""

    async def touch(self, code: str, user_id: UUID, now_ms: int) -> None:
        """Отметить игрока на связи."""
        ...

    async def seen(self, code: str) -> dict[UUID, int]:
        """Последний heartbeat каждого игрока."""
        ...


class PlayerRooms(Protocol):
    """Активная комната игрока ``player:{id}:room`` (D10: одна активная комната)."""

    async def get(self, user_id: UUID) -> str | None:
        """Код активной комнаты."""
        ...

    async def claim(self, user_id: UUID, code: str) -> bool:
        """Занять (если свободно или уже эта комната)."""
        ...

    async def assign(self, user_id: UUID, code: str) -> None:
        """Записать безусловно (прошлая комната проверена и неактивна)."""
        ...

    async def release(self, user_id: UUID, code: str) -> None:
        """Освободить, если записана эта комната."""
        ...


# ----------------------------------------------------------------------------- внешние
@dataclass(frozen=True, slots=True)
class RoundPlanRequest:
    """Запрос плана матча (``RoundPlanRequest`` internal API game, D5)."""

    mode_key: str
    version: int
    seed: str
    rounds: int
    collection_ids: tuple[UUID, ...] = ()
    exclude_ids: tuple[UUID, ...] = ()


class RoundPlanPort(Protocol):
    """План раундов от game (D5); фейк — ``gv_testkit.match.FakeRoundPlanPort``.

    Ошибки реализаций — ``AppError`` с кодом ``game.*`` или ``match.*``; application
    переводит их в коды match (``owner.plan_error``): ``match.pool_too_small``,
    ``match.collection_not_found``, ``match.game_unavailable`` и т. д.
    """

    async def create_round_plan(self, request: RoundPlanRequest) -> RoundPlan:
        """Разыграть план матча."""
        ...


class ModeSource(Protocol):
    """Опубликованные онлайн-шаблоны: текущая версия и тело (версии неизменяемы)."""

    async def current(self, key: str) -> int | None:
        """Текущая опубликованная версия (``None`` — нет или в архиве)."""
        ...

    async def body(self, key: str, version: int) -> Json | None:
        """Тело версии конфига."""
        ...


class RealtimePublisher(Protocol):
    """Публикация в канал Centrifugo (ADR 0035): ошибка не ретраится — клиент ресинкнет."""

    async def publish(self, channel: str, message: Json) -> None:
        """Опубликовать сообщение ``room.message.v1``."""
        ...


class SubscriptionTokens(Protocol):
    """Subscription-токены каналов комнат (D9, свой секрет)."""

    def issue(self, user_id: UUID, channel: str, now: datetime) -> tuple[str, datetime]:
        """Токен и момент истечения."""
        ...


class RateLimiter(Protocol):
    """Лимиты частоты (§12 п. 4)."""

    async def hit(
        self, bucket: str, subject: str, *, limit: int, window_s: int, fail_closed: bool = False
    ) -> None:
        """Учесть запрос.

        Raises:
            MatchRateLimitedError: лимит превышен.
            MatchUnavailableError: ``fail_closed`` и хранилище лимитов недоступно.
        """
        ...


class IdempotentCreates(Protocol):
    """Ключи ``Idempotency-Key`` создания комнаты (комната живёт в Redis, не в Postgres)."""

    async def get(self, user_id: UUID, key: str) -> tuple[str, str] | None:
        """``(отпечаток запроса, код комнаты)`` или ``None``."""
        ...

    async def put(self, user_id: UUID, key: str, fingerprint: str, code: str) -> None:
        """Запомнить результат."""
        ...


# ----------------------------------------------------------------------------- Postgres
@dataclass(frozen=True, slots=True)
class MatchRow:
    """Итог матча для чтения (``GET /v1/matches/{id}``)."""

    match_id: UUID
    room_code: str
    mode_key: str
    mode_version: int
    domain: str
    format: str
    ranked: bool
    title_key: str
    clue_type: str
    answer_format: str
    started_at: datetime
    finished_at: datetime
    finish: str
    collection_ids: list[UUID]
    players: list[Json]
    rounds: list[Json]


@dataclass(frozen=True, slots=True)
class MyMatchRow:
    """Строка «Мои матчи»."""

    match_id: UUID
    mode_key: str
    mode_version: int
    domain: str
    format: str
    ranked: bool
    title_key: str
    clue_type: str
    answer_format: str
    finished_at: datetime
    finish: str
    players: int
    place: int
    score: int
    outcome: str | None
    my_finish: str


class MatchRecords(Protocol):
    """История матчей в Postgres: итог одной транзакцией с outbox (D8)."""

    async def record_started(self, event: MatchStarted, meta: Json) -> bool:
        """Строка матча и ``MatchStarted``; ``False`` — уже записано (повтор)."""
        ...

    async def record_finished(self, room: Room, match: MatchState) -> bool:
        """Итог, игроки, раунды и ``MatchFinished``; ``False`` — уже записано."""
        ...

    async def get(self, match_id: UUID) -> MatchRow | None:
        """Завершённый матч."""
        ...

    async def mine(
        self, user_id: UUID, cursor: tuple[datetime, UUID] | None, limit: int
    ) -> list[MyMatchRow]:
        """Матчи пользователя, новые первыми."""
        ...

    async def participants(self, match_id: UUID) -> list[UUID] | None:
        """Участники завершённого матча (``None`` — нет такого или ещё идёт)."""
        ...

    async def rate(self, event: PlayerRated, *, actor_id: UUID, key: str, fingerprint: str) -> bool:
        """Сохранить оценку и ``PlayerRated`` (идемпотентно по ключу); ``False`` — повтор ключа.

        Raises:
            MatchConflictError: ``match.already_rated``; ``idempotency.key_reused``.
        """
        ...


class Users(Protocol):
    """Проекция профилей identity (ники, удаление)."""

    async def nickname(self, user_id: UUID) -> str | None:
        """Ник пользователя."""
        ...

    async def nicknames(self, user_ids: Sequence[UUID]) -> Mapping[UUID, str]:
        """Ники нескольких пользователей."""
        ...
