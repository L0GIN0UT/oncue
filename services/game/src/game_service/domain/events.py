"""Доменные события game (контракты ``contracts/events/game/*.v1.json``).

Поля событий совпадают со схемами один в один (``additionalProperties: false``): payload
собирает ``DomainEvent.payload``, проверку делает contract-тест продюсера. ПДн (e-mail,
ник) в события не попадают (§9 п. 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from gv_core.domain import DomainEvent

SESSION_AGGREGATE = "session"
MODE_CONFIG_AGGREGATE = "mode_config"


@dataclass(frozen=True, kw_only=True)
class RoundSummary:
    """Раунд в ``GameFinished.rounds``.

    Attributes:
        index: Номер раунда с 0.
        media_item_id: Загаданная единица.
        outcome: ``exact`` | ``partial`` | ``miss`` | ``gave_up`` | ``void``.
        level: Уровень точного (или частичного) ответа; ``None`` — не угадан.
        points: Очки за раунд.
        elapsed_ms: От выдачи первого ассета до итога; ``None`` — ассет не выдавался.
    """

    index: int
    media_item_id: UUID
    outcome: str
    level: int | None
    points: int
    elapsed_ms: int | None


@dataclass(frozen=True, kw_only=True)
class GameFinished(DomainEvent):
    """Соло-сессия завершена (все игроки, включая гостей).

    ``collection_id``/``collection_version`` — у сессии по сборнику (этап 3b, D9: «пройден»
    и сложность считает collections), иначе ``None``.
    ``collection_tags`` - теги сборника на старте (ADR 0067), пусто - нет тегов или не сборник.
    """

    aggregate_type: ClassVar[str] = SESSION_AGGREGATE

    session_id: UUID
    version: int
    user_id: UUID | None
    is_guest: bool
    mode_key: str
    mode_version: int
    domain: str
    source_type: str
    clue_type: str
    day: str | None
    score: int
    max_score: int
    started_at: datetime
    finished_at: datetime
    rounds: tuple[RoundSummary, ...]
    collection_id: UUID | None = None
    collection_version: int | None = None
    collection_tags: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class DailyCompleted(DomainEvent):
    """Пользователь (не гость) завершил дейлик."""

    aggregate_type: ClassVar[str] = SESSION_AGGREGATE

    session_id: UUID
    version: int
    user_id: UUID
    mode_key: str
    mode_version: int
    domain: str
    day: str
    score: int
    max_score: int
    exact_count: int
    rounds_count: int


@dataclass(frozen=True, kw_only=True)
class ModeConfigPublished(DomainEvent):
    """Опубликована новая неизменяемая версия конфига (в том числе откат)."""

    aggregate_type: ClassVar[str] = MODE_CONFIG_AGGREGATE

    mode_key: str
    version: int
    domain: str
    body_hash: str
    actor_id: UUID | None
    published_at: datetime
    rolled_back_from: int | None


@dataclass(frozen=True, kw_only=True)
class ModeConfigArchived(DomainEvent):
    """Режим убран из каталога режимов; начатые сессии доигрываются."""

    aggregate_type: ClassVar[str] = MODE_CONFIG_AGGREGATE

    mode_key: str
    version: int
    actor_id: UUID | None
    archived_at: datetime


@dataclass(frozen=True, kw_only=True)
class BracketPickSummary:
    """Выбор в ``BracketCompleted.picks``.

    Attributes:
        stage: Размер тура (32, 16 … 2).
        winner_id: Выбранная единица.
        loser_id: Единица, проигравшая выбор.
    """

    stage: int
    winner_id: UUID
    loser_id: UUID


@dataclass(frozen=True, kw_only=True)
class BracketCompleted(DomainEvent):
    """Игрок прошёл сетку предпочтений периода до чемпиона (ADR 0027).

    Сетка не публикует ``GameFinished``: правильного ответа и очков у неё нет.
    """

    aggregate_type: ClassVar[str] = SESSION_AGGREGATE

    session_id: UUID
    version: int
    user_id: UUID | None
    is_guest: bool
    mode_key: str
    mode_version: int
    period_start: str
    champion_id: UUID
    picks: tuple[BracketPickSummary, ...]


@dataclass(frozen=True, kw_only=True)
class ClueAssetsRequested(DomainEvent):
    """Админ запросил ассеты под параметры загадки черновика (ADR 0025)."""

    aggregate_type: ClassVar[str] = MODE_CONFIG_AGGREGATE

    request_id: UUID
    mode_key: str
    draft_revision: int
    domain: str
    clue_type: str
    # Any: параметры загадки — JSON по contracts/clues/<clue_type>.params.v1.json.
    params: dict[str, Any]
    params_hash: str
    actor_id: UUID
    requested_at: datetime
