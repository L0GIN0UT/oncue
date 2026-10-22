"""Значения домена комнат и матча (план этапа 4, D2–D14).

Время в состоянии — целые миллисекунды Unix (``*_ms``): состояние целиком уходит в снимок
Redis (ADR 0034), а таймеры считаются по ``phase_ends_at_ms`` после захвата комнаты другим
инстансом. Часы — только аргументом ``now_ms`` (порт ``Clock`` у application).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from gv_game_kernel import ContestRoundState

SCHEMA_VERSION = 1
"""Версия формата снимка комнаты; новый инстанс обязан читать предыдущую (§13 плана)."""


class Phase(StrEnum):
    """Фаза комнаты (``Phase`` контракта API и ``PhaseDelta`` канала)."""

    LOBBY = "lobby"
    COUNTDOWN = "countdown"
    LEVEL_OPEN = "level_open"
    LEVEL_CLOSED = "level_closed"
    REVEAL = "reveal"
    SUMMARY = "summary"
    PAUSED = "paused"
    CLOSED = "closed"


MATCH_PHASES = frozenset(
    {Phase.COUNTDOWN, Phase.LEVEL_OPEN, Phase.LEVEL_CLOSED, Phase.REVEAL, Phase.PAUSED}
)
"""Фазы идущего матча (вход посторонних запрещён, уход — выбывание)."""


class Presence(StrEnum):
    """Присутствие игрока (D10)."""

    ONLINE = "online"
    DISCONNECTED = "disconnected"
    LEFT = "left"


class MatchFinish(StrEnum):
    """Как закончился матч (``MatchResult.finish``)."""

    COMPLETED = "completed"
    FORFEIT = "forfeit"
    TECH_WIN = "tech_win"
    ABORTED = "aborted"


class PlayerFinish(StrEnum):
    """Как закончил матч игрок (``players[].finish`` в ``MatchFinished``)."""

    COMPLETED = "completed"
    FORFEIT = "forfeit"
    LEFT = "left"
    TECH_WIN = "tech_win"
    TECH_LOSS = "tech_loss"


# ----------------------------------------------------------------------------- шаблон
@dataclass(frozen=True, slots=True)
class ModeInfo:
    """Шаблон режима комнаты (версия фиксируется при создании).

    Attributes:
        key: Ключ режима.
        version: Версия конфига.
        domain: ``music`` | ``film``.
        kind: ``session.kind``: ``duel`` | ``party`` | ``lobby``.
        title_key: Ключ i18n названия.
        ranked: Рейтинговый режим (только очередь подбора, этап 4b).
        ladder: Ladder рейтинга.
    """

    key: str
    version: int
    domain: str
    kind: str
    title_key: str
    ranked: bool = False
    ladder: str | None = None


@dataclass(frozen=True, slots=True)
class SettingsOptions:
    """Что хост кастомного лобби может выбрать (``session.lobby.overridable``, D12).

    ``None`` — поле не переопределяется.
    """

    rounds: tuple[int, ...] | None = None
    level_window_s: tuple[int, ...] | None = None
    answer_format: tuple[str, ...] | None = None
    clue_type: tuple[str, ...] | None = None
    players_max: tuple[int, ...] | None = None
    visibility: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ChatRules:
    """Эмоции, готовые фразы и свободный чат (``session.chat``, D14)."""

    emotes: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()
    free_text: bool = False


@dataclass(frozen=True, slots=True)
class RoomTemplate:
    """Значения шаблона, нужные комнате до старта матча.

    Attributes:
        players_min: Минимум игроков для старта.
        players_max: Мест по шаблону.
        rounds: Раундов по шаблону.
        level_windows_s: Окна уровней шаблона.
        answer_format: Формат ответа шаблона.
        clue_type: Тип загадки шаблона.
        visibility: Видимость по умолчанию.
        options: Переопределения хоста (только у кастомного лобби).
        chat: Правила чата.
        uses_collection: Источник шаблона — сборники (нужны ``collection_ids``).
        max_collections: Сколько сборников можно выбрать.
    """

    players_min: int
    players_max: int
    rounds: int
    level_windows_s: tuple[int, ...]
    answer_format: str
    clue_type: str
    visibility: str
    options: SettingsOptions | None
    chat: ChatRules
    uses_collection: bool = False
    max_collections: int = 0


@dataclass(frozen=True, slots=True)
class RoomSettings:
    """Действующие настройки комнаты.

    Attributes:
        level_window_s: Окно уровня, выбранное хостом; ``None`` — окна шаблона.
    """

    rounds: int
    level_window_s: int | None
    answer_format: str
    clue_type: str
    players_max: int
    visibility: str
    collection_ids: tuple[UUID, ...] = ()


@dataclass(slots=True)
class Seat:
    """Место игрока в комнате."""

    user_id: UUID
    slot: int
    nickname: str
    joined_at_ms: int
    presence: Presence = Presence.ONLINE
    away: bool = False
    ready: bool = False


# ----------------------------------------------------------------------------- план
@dataclass(frozen=True, slots=True)
class PersonKey:
    """Персона единицы раунда (ключ частичного ответа и ревил)."""

    person_id: UUID
    name: str
    normalized: str
    role: str


@dataclass(frozen=True, slots=True)
class AnswerKey:
    """Ключ ответа раунда из плана game (``AnswerKey`` internal API game)."""

    media_item_id: UUID
    domain: str
    kind: str
    title: str
    year: int | None
    aliases: tuple[str, ...]
    title_aliases: tuple[str, ...]
    persons: tuple[PersonKey, ...]
    partial: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LevelAsset:
    """Набор ассетов уровня: ссылку match получает в момент открытия уровня (D11)."""

    level: int
    asset_set_id: UUID


@dataclass(frozen=True, slots=True)
class PlanOption:
    """Вариант формата ``pick`` (``option_id`` непрозрачен)."""

    option_id: str
    label_key: str | None = None
    media_item_id: UUID | None = None
    title: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedRound:
    """Раунд плана матча — хранится только в снимке, клиенту не уходит (D5)."""

    index: int
    answer: AnswerKey
    levels: tuple[LevelAsset, ...]
    options: tuple[PlanOption, ...] = ()
    reveal_asset_set_id: UUID | None = None

    def asset_for(self, level: int) -> UUID | None:
        """Набор ассетов уровня ``level`` или ``None``."""
        return next((a.asset_set_id for a in self.levels if a.level == level), None)


@dataclass(frozen=True, slots=True)
class RoundPlan:
    """План матча от game (D5).

    Attributes:
        mode_version: Версия конфига, по которой разыгран план.
        body: Тело конфига режима (``ModeConfig``) — правила матча.
        seed: Seed розыгрыша.
        rounds: Раунды.
        collections: Сборники с зафиксированной версией состава.
    """

    mode_version: int
    body: dict[str, Any]  # Any: тело ModeConfig по contracts/modes, разбирает ядро
    seed: str
    rounds: tuple[PlannedRound, ...]
    collections: tuple[tuple[UUID, int], ...] = ()


@dataclass(frozen=True, slots=True)
class Clue:
    """Подача уровня или ревила (``LevelClue`` контракта).

    Attributes:
        meta: Параметры уровня для UI (``ClueMeta``).
    """

    level: int
    kind: str
    url: str
    expires_at_ms: int
    mime: str
    meta: dict[str, Any] = field(default_factory=dict)  # Any: числа и строки ClueMeta


# ----------------------------------------------------------------------------- матч
@dataclass(frozen=True, slots=True)
class PlayerRoundResult:
    """Результат игрока в раунде (``rounds[].results[]`` события ``MatchFinished``)."""

    user_id: UUID
    level: int | None
    partial: bool
    score: int
    elapsed_ms: int | None


@dataclass(frozen=True, slots=True)
class RoundRecord:
    """Сыгранный раунд."""

    index: int
    media_item_id: UUID
    void: bool
    results: tuple[PlayerRoundResult, ...]


@dataclass(slots=True)
class MatchPlayer:
    """Игрок матча.

    Attributes:
        finish: Как игрок закончил матч (``None`` — ещё играет).
        disconnected_at_ms: Когда игрок пропал (для ``disconnect_grace_s``).
    """

    user_id: UUID
    slot: int
    score: int = 0
    finish: PlayerFinish | None = None
    disconnected_at_ms: int | None = None

    @property
    def active(self) -> bool:
        """Игрок ещё в матче."""
        return self.finish is None


@dataclass(frozen=True, slots=True)
class Paused:
    """Пауза дуэли при отключении (D10): во что вернуться и сколько оставалось."""

    phase: Phase
    remaining_ms: int | None


@dataclass(frozen=True, slots=True)
class Standing:
    """Место игрока в итоге матча."""

    user_id: UUID
    slot: int
    score: int
    place: int
    outcome: str | None
    finish: PlayerFinish


@dataclass(slots=True)
class MatchState:
    """Матч комнаты.

    Attributes:
        match_id: Идентификатор матча.
        seed: Seed плана.
        plan: План (только снимок, D5).
        body: Тело конфига плана с переопределениями хоста.
        round_index: Текущий раунд (с 0).
        contest: Состояние раунда ядра (``ContestRoundState``).
        clue: Ссылка открытого уровня (одна на комнату, раунд и уровень, D11).
        final_clue: Ассет ревила текущего раунда.
        started_at_ms: Когда кончился отсчёт (``MatchStarted``).
        started_written: ``MatchStarted`` записан в outbox.
        finished_written: Итог и ``MatchFinished`` записаны в Postgres.
        grace_ms: ``disconnect_grace_s`` режима.
        levels_count: Уровней загадки (виды строятся без ядра — в процессе api).
        attempts_per_level: Ответов на уровень.
        statuses: Статусы игроков в раунде (``PlayerStatus``), обновляет владелец.
        clue_pending: Уровень открыт, ссылка ещё запрашивается (ходы и закрытие ждут).
        paused_at_ms: Когда началась пауза (сдвиг окна уровня при возобновлении).
    """

    match_id: UUID
    seed: str
    mode_version: int
    body: dict[str, Any]  # Any: тело ModeConfig, разбирает ядро
    plan: tuple[PlannedRound, ...]
    collections: tuple[tuple[UUID, int], ...]
    players: dict[UUID, MatchPlayer]
    created_at_ms: int
    round_index: int = 0
    contest: ContestRoundState | None = None
    clue: Clue | None = None
    final_clue: Clue | None = None
    records: list[RoundRecord] = field(default_factory=list)
    finish: MatchFinish | None = None
    abort_reason: str | None = None
    paused: Paused | None = None
    started_at_ms: int | None = None
    finished_at_ms: int | None = None
    standings: tuple[Standing, ...] = ()
    started_written: bool = False
    finished_written: bool = False
    grace_ms: int = 0
    levels_count: int = 1
    attempts_per_level: int = 1
    statuses: dict[str, str] = field(default_factory=dict)
    clue_pending: bool = False
    paused_at_ms: int | None = None

    @property
    def rounds_total(self) -> int:
        """Раундов в плане."""
        return len(self.plan)

    @property
    def current(self) -> PlannedRound:
        """Текущий раунд плана."""
        return self.plan[self.round_index]

    def active_ids(self) -> list[UUID]:
        """Игроки, ещё участвующие в матче (по слоту)."""
        return [p.user_id for p in sorted(self.players.values(), key=lambda p: p.slot) if p.active]


@dataclass(frozen=True, slots=True)
class Change:
    """Изменение комнаты для канала ``room:{code}`` (вид — ``kind`` сообщения).

    Attributes:
        version: Версия комнаты после изменения (монотонна).
        data: Данные вида, которые нельзя восстановить из итогового состояния (номер
            раунда и уровня, текст чата); полный вид строит application.
    """

    kind: str
    version: int
    data: dict[str, Any] = field(default_factory=dict)  # Any: примитивы JSON
