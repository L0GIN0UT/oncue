"""Схемы публичного API match (план этапа 4, §2.5, D6–D14) — контракт для фронтенда и bff.

Античит (§12 п. 1): до фазы ``reveal`` раунда в видах комнаты и матча нет ``media_item_id``,
названия, ключей ответа и плана; ссылка уровня (``clue``) появляется только после открытия
уровня; ответы соперников видны только статусом (``thinking|answered|missed|guessed``).
Личное (свои попытки, варианты ``pick``) — только в ответе команды и в ``GET /state``
(поле ``me``), в канал Centrifugo не уходит (D9).

Команды комнаты несут ``op_id`` (идемпотентность в окне снимка комнаты), команды фазы —
ещё и ``expected_version``. Если владелец комнаты не ответил за
``GV_MATCH__REPLY_TIMEOUT_MS``, команда принята в поток комнаты — 202 ``CommandAccepted``,
результат придёт дельтой канала (D6).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ROOM_CODE_PATTERN = r"^[A-HJ-NP-Z2-9]{8}$"
"""Код комнаты (D12): 8 символов без 0/O/1/I."""
RoomCode = Annotated[str, StringConstraints(pattern=ROOM_CODE_PATTERN)]
OpId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{8,128}$")]
"""Идентификатор команды (как ``Idempotency-Key``): повтор с тем же ``op_id`` — тот же ответ."""
ModeKey = Annotated[
    str, StringConstraints(pattern=r"^(music|film)\.[a-z0-9_]+\.[a-z0-9_]+$", max_length=100)
]
PluginKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
I18nKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]*$", max_length=100)]
OptionId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,32}$")]
Cursor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,512}$")]

Domain = Literal["music", "film"]
RoomKind = Literal["duel", "party", "lobby"]
Visibility = Literal["public", "private"]
Phase = Literal[
    "lobby",
    "countdown",
    "level_open",
    "level_closed",
    "reveal",
    "summary",
    "paused",
    "closed",
]
Presence = Literal["online", "disconnected", "left"]
"""Как ``PlayerDelta.presence`` в ``contracts/realtime/room.message.v1.json``."""
PlayerRoundStatus = Literal["thinking", "answered", "missed", "guessed"]
GuessResultLiteral = Literal["exact", "partial", "miss"]
ClueKind = Literal["audio", "image", "text"]
MatchFinish = Literal["completed", "forfeit", "tech_win", "aborted"]
PlayerOutcome = Literal["win", "loss", "draw"]
PlayerFinish = Literal["completed", "forfeit", "left", "tech_win", "tech_loss"]
MAX_PLAYERS = 6
MAX_ROUNDS = 50
MAX_LEVELS = 20
MAX_COLLECTIONS = 10
"""Потолок числа сборников комнаты в схеме; рабочий предел — ``max_collections`` режима."""
CHAT_MAX_LENGTH = 200


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Command(_Request):
    """Команда комнаты: ``op_id`` обязателен."""

    op_id: OpId


class _PhaseCommand(_Command):
    """Команда фазы: ``op_id`` и ``expected_version`` (иначе 409 ``match.phase_mismatch``)."""

    expected_version: int = Field(ge=0, description="`version` комнаты, которую видит клиент")


# ============================================================================ комната
class ModeRef(BaseModel):
    """Шаблон режима комнаты (версия фиксируется при создании)."""

    key: str
    version: int = Field(ge=1)
    domain: Domain
    kind: RoomKind
    title_key: str
    clue_type: str
    answer_format: str
    ranked: bool = False


class RoomSettings(BaseModel):
    """Действующие настройки комнаты (значения шаблона с переопределениями хоста)."""

    rounds: int = Field(ge=1, le=MAX_ROUNDS)
    level_window_s: int = Field(ge=1, description="Окно уровня, с (у шаблона — первое окно)")
    answer_format: str
    clue_type: str
    players_max: int = Field(ge=2, le=MAX_PLAYERS)
    visibility: Visibility
    collection_ids: list[UUID] = Field(default_factory=list, max_length=MAX_COLLECTIONS)


class SettingsOptions(BaseModel):
    """Что хост может выбрать (``session.lobby.overridable`` шаблона, D12); ``null`` — нельзя.

    ``answer_format`` и ``clue_type`` — разрешённые шаблоном варианты (ADR 0070): game строит
    план под выбранные; несовместимое сочетание — 422 ``match.setting_not_allowed``
    (``details.reason = incompatible``) при старте.
    """

    rounds: list[int] | None = None
    level_window_s: list[int] | None = None
    answer_format: list[str] | None = None
    clue_type: list[str] | None = None
    players_max: list[int] | None = None
    visibility: list[Visibility] | None = None
    max_collections: int | None = Field(default=None, ge=0, le=MAX_COLLECTIONS)


class ChatOptions(BaseModel):
    """Эмоции, готовые фразы и свободный чат комнаты (D14)."""

    emotes: list[str]
    phrases: list[str]
    free_text: bool = Field(description="Свободный чат — только в кастомном лобби")


class PlayerSlot(BaseModel):
    """Место игрока в комнате."""

    slot: int = Field(ge=0, lt=MAX_PLAYERS)
    user_id: UUID
    nickname: str
    is_host: bool
    presence: Presence
    away: bool = Field(default=False, description="Игрок отметил «отошёл» (`match/away`)")
    ready: bool = Field(description="Готовность в лобби; сбрасывается при смене настроек")
    score: int = Field(default=0, ge=0, description="Очки в текущем матче")
    listen_s: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Сколько секунд игрок слушает открытый уровень (округлено, не больше длины "
            "подачи); `null` — неизвестно или не фаза `level_open`. Эфемерно, ответа не раскрывает"
        ),
    )


class StartError(BaseModel):
    """Последний отказ старта матча (план game не получен) — виден всем в лобби."""

    code: str = Field(description="`match.game_unavailable`, `match.pool_too_small` и т. п.")
    at: datetime


class RoomView(BaseModel):
    """Комната для всех участников (то же, что в дельтах канала ``room:{code}``).

    ``phase_ends_at`` и ``server_now`` — для таймера клиента с поправкой часов.
    """

    code: RoomCode
    visibility: Visibility
    mode: ModeRef
    host_id: UUID
    players: list[PlayerSlot] = Field(max_length=MAX_PLAYERS)
    players_min: int = Field(ge=2, le=MAX_PLAYERS)
    settings: RoomSettings
    options: SettingsOptions | None = Field(
        default=None, description="Только у кастомного лобби (`mode.kind = lobby`)"
    )
    chat: ChatOptions
    phase: Phase
    phase_ends_at: datetime | None = Field(
        default=None, description="Когда сервер закроет фазу; null — без таймера (лобби)"
    )
    match_id: UUID | None = Field(default=None, description="Текущий или последний матч")
    memory_size: int = Field(
        default=0, ge=0, description="Сколько единиц в памяти сыгранного лобби (`exclude_ids`)"
    )
    rematch_code: RoomCode | None = Field(
        default=None,
        description=(
            "Код комнаты реванша, когда согласились все (у кастомного лобби — свой код: "
            "комната возвращается в лобби); старая комната дуэли и party закрывается"
        ),
    )
    start_error: StartError | None = Field(
        default=None,
        description=(
            "Почему не начался матч (старт мог ответить 202 до отказа game); "
            "сбрасывается при следующем старте или смене настроек"
        ),
    )
    version: int = Field(ge=0)
    server_now: datetime


class OpenRoomCard(BaseModel):
    """Открытое лобби в списке (``visibility = public``, фаза ``lobby``)."""

    code: RoomCode
    mode: ModeRef
    host_nickname: str
    players: int = Field(ge=0, le=MAX_PLAYERS)
    players_max: int = Field(ge=2, le=MAX_PLAYERS)
    settings: RoomSettings
    created_at: datetime


class OpenRoomsPage(BaseModel):
    """Страница открытых лобби (keyset-курсор)."""

    items: list[OpenRoomCard]
    next_cursor: str | None = None


class SettingsOverride(_Request):
    """Переопределения хоста; каждое значение — из ``SettingsOptions`` шаблона (D12)."""

    rounds: int | None = Field(default=None, ge=1, le=MAX_ROUNDS)
    level_window_s: int | None = Field(default=None, ge=1, le=300)
    answer_format: PluginKey | None = None
    clue_type: PluginKey | None = None
    players_max: int | None = Field(default=None, ge=2, le=MAX_PLAYERS)
    visibility: Visibility | None = None


class CreateRoomRequest(_Request):
    """Создание комнаты по онлайн-шаблону (``session.kind`` ∈ duel/party/lobby)."""

    mode_key: ModeKey
    settings: SettingsOverride | None = None
    collection_ids: list[UUID] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_COLLECTIONS,
        description="Сборники для шаблона с source `collection` (≤ `max_collections` режима)",
    )


class JoinRequest(_Command):
    """Войти в комнату по коду."""


class LeaveRequest(_Command):
    """Выйти из комнаты (во время матча — выбыть, очки сохраняются)."""


class ReadyRequest(_Command):
    """Отметить готовность в лобби."""

    ready: bool


class SettingsRequest(_Command):
    """Сменить настройки (только хост, только в лобби); готовность всех сбрасывается."""

    settings: SettingsOverride | None = None
    collection_ids: list[UUID] | None = Field(
        default=None, min_length=1, max_length=MAX_COLLECTIONS
    )


class TargetPlayerRequest(_Command):
    """Команда хоста над другим игроком (кик, передача хоста)."""

    user_id: UUID


class ResetMemoryRequest(_Command):
    """Сбросить память сыгранных единиц лобби (только хост)."""


class StartRequest(_PhaseCommand):
    """Начать матч (только хост, все готовы, игроков ≥ ``players_min``)."""


class RematchRequest(_Command):
    """Реванш: новая комната с теми же игроками по согласию (D19)."""


class CommandAccepted(BaseModel):
    """202: команда в потоке комнаты, владелец не ответил вовремя (D6)."""

    accepted: Literal[True] = True
    version: int = Field(ge=0, description="Последняя известная `version` комнаты")


class RoomCommandResult(BaseModel):
    """Ответ команды комнаты: новое состояние комнаты."""

    version: int = Field(ge=0)
    room: RoomView


class RematchResult(BaseModel):
    """Реванш: код новой комнаты (``null`` — ждём согласия остальных)."""

    version: int = Field(ge=0)
    room: RoomView
    rematch_code: RoomCode | None = None


class RealtimeSubscription(BaseModel):
    """Subscription-токен Centrifugo для канала комнаты (D9, отдельный секрет подписок)."""

    channel: str = Field(pattern=r"^room:[A-HJ-NP-Z2-9]{8}$")
    token: str
    expires_at: datetime


# ============================================================================ матч
class ClueMeta(BaseModel):
    """Параметры уровня загадки для UI (``hint_fields`` плагина ядра)."""

    seconds: float | None = None
    blur_px: float | None = None
    crop: float | None = None
    pixel_block: int | None = None
    frame: int | None = None
    fields: list[str] | None = None
    count: int | None = None


class LevelClue(BaseModel):
    """Ссылка на ассет открытого уровня (одна на комнату, раунд и уровень, D11)."""

    level: int = Field(ge=1, le=MAX_LEVELS)
    kind: ClueKind
    url: str
    expires_at: datetime
    mime: str
    meta: ClueMeta


class Artwork(BaseModel):
    """Картинка ответа в ревиле (ADR 0073)."""

    url: str
    expires_at: datetime
    mime: str


class MatchPlayerView(BaseModel):
    """Игрок в матче для всех зрителей: статус без текста ответа."""

    user_id: UUID
    status: PlayerRoundStatus | None = Field(description="null вне фазы уровня")
    score: int = Field(ge=0)
    round_points: int | None = Field(default=None, description="Очки за раунд — с фазы reveal")


class PersonOut(BaseModel):
    """Персона в ревиле."""

    name: str
    role: str


class RoundReveal(BaseModel):
    """Ответ раунда — только в фазе ``reveal`` и позже."""

    media_item_id: UUID
    title: str
    year: int | None
    persons: list[PersonOut]
    final_clue: LevelClue | None = Field(description="null — ассет ревила недоступен")
    artwork: Artwork | None = Field(
        default=None,
        description=(
            "Картинка ответа (обложка, постер; ADR 0073): подписанная короткоживущая ссылка; "
            "`null` — картинки нет или она недоступна"
        ),
    )


class MatchView(BaseModel):
    """Матч для любого участника (и в дельтах канала): без плана и ключей ответа."""

    match_id: UUID
    round_index: int = Field(ge=0, lt=MAX_ROUNDS)
    rounds_total: int = Field(ge=1, le=MAX_ROUNDS)
    level: int | None = Field(default=None, ge=1, le=MAX_LEVELS, description="Открытый уровень")
    levels_count: int = Field(ge=1, le=MAX_LEVELS)
    clue: LevelClue | None = Field(
        default=None, description="Только после открытия уровня (`level_opened`)"
    )
    players: list[MatchPlayerView] = Field(max_length=MAX_PLAYERS)
    reveal: RoundReveal | None = Field(default=None, description="Только в фазе reveal")


class OwnGuess(BaseModel):
    """Своя попытка (видна только себе)."""

    level: int = Field(ge=1, le=MAX_LEVELS)
    kind: Literal["item", "text", "option", "skip"]
    display: str
    result: GuessResultLiteral | None = Field(description="null у пропуска")


class OptionOut(BaseModel):
    """Вариант ответа формата ``pick``: ``option_id`` непрозрачен и перемешан на комнату."""

    option_id: OptionId
    label_key: str | None = None
    title: str | None = None


class MyRoundView(BaseModel):
    """Личное состояние раунда: только в ответе команды и в ``GET /state`` (D9)."""

    round_index: int = Field(ge=0, lt=MAX_ROUNDS)
    status: PlayerRoundStatus | None
    attempts_left: int = Field(ge=0)
    guesses: list[OwnGuess]
    options: list[OptionOut] = Field(default_factory=list)


class RoomState(BaseModel):
    """Полный снимок для зрителя-участника: ресинк после пропуска версии."""

    room: RoomView
    match: MatchView | None = None
    me: MyRoundView | None = Field(default=None, description="Только своё; null вне матча")


class TickRequest(_Request):
    """Heartbeat присутствия и HTTP-фолбэк снимка (D10)."""

    known_version: int | None = Field(
        default=None, ge=0, description="Версия у клиента; снимок вернётся, если есть новее"
    )


class TickResult(BaseModel):
    """Ответ tick: ``state`` есть, только если версия комнаты новее ``known_version``."""

    version: int = Field(ge=0)
    server_now: datetime
    state: RoomState | None = None


class ItemAnswer(_Request):
    """Ответ выбором из автокомплита каталога."""

    media_item_id: UUID


class TextAnswer(_Request):
    """Ответ свободным текстом."""

    text: str = Field(min_length=1, max_length=120)


class OptionAnswer(_Request):
    """Ответ вариантом (формат ``pick``)."""

    option_id: OptionId


class GuessRequest(_PhaseCommand):
    """Ответ на открытом уровне раунда."""

    round_index: int = Field(ge=0, lt=MAX_ROUNDS)
    level: int = Field(ge=1, le=MAX_LEVELS)
    answer: ItemAnswer | TextAnswer | OptionAnswer


class SkipRequest(_PhaseCommand):
    """Пропустить открытый уровень (ждать следующего)."""

    round_index: int = Field(ge=0, lt=MAX_ROUNDS)
    level: int = Field(ge=1, le=MAX_LEVELS)


class GuessResult(BaseModel):
    """Итог своего ответа (время и скорость считает только сервер)."""

    result: GuessResultLiteral | None = Field(description="null у пропуска")
    attempts_left: int = Field(ge=0)
    level_closed: bool = Field(description="Уровень закрыт для меня (угадал или попытки кончились)")
    version: int = Field(ge=0)
    me: MyRoundView


class ForfeitRequest(_Command):
    """Сдаться (дуэль — поражение «Соперник сдался», party — выбыть)."""


class EmoteRequest(_Command):
    """Эмоция или готовая фраза из ``chat.emotes`` / ``chat.phrases`` режима."""

    kind: Literal["emote", "phrase"]
    key: I18nKey


class ChatRequest(_Command):
    """Свободный чат (только кастомное лобби): ≤ 200 символов, без ссылок, denylist 3b."""

    text: str = Field(min_length=1, max_length=CHAT_MAX_LENGTH)


class AwayRequest(_Command):
    """Отметка «отошёл» / «вернулся»."""

    away: bool


class ListenRequest(_Command):
    """Позиция прослушивания уровня (для метки «соперник слушает N с»)."""

    level: int = Field(ge=1, le=MAX_LEVELS)
    position_ms: int = Field(ge=0, description="Позиция воспроизведения; сверх длины зажимается")


class MatchCommandResult(BaseModel):
    """Ответ команд матча без личного итога (skip, forfeit, emote, chat, away)."""

    version: int = Field(ge=0)


# ============================================================================ итог
class RateRequest(_Request):
    """Похвала или жалоба на игрока после матча (``PlayerRated``, обработка — этап 5)."""

    to_user_id: UUID
    kind: Literal["praise", "report"]


class ResultPlayer(BaseModel):
    """Игрок в итоге матча (§2.2: ``place``, ``outcome``, ``finish``)."""

    user_id: UUID
    nickname: str
    slot: int = Field(ge=0, lt=MAX_PLAYERS)
    place: int = Field(ge=1, le=MAX_PLAYERS, description="Место с ничьими: 1, 1, 3")
    score: int = Field(ge=0)
    outcome: PlayerOutcome | None = Field(description="Только у дуэли")
    finish: PlayerFinish


class RoundPlayerResult(BaseModel):
    """Результат игрока в раунде."""

    user_id: UUID
    level: int | None = Field(description="Уровень угадывания; null — не угадал")
    partial: bool
    score: int = Field(ge=0)
    elapsed_ms: int | None = Field(default=None, ge=0, description="От открытия уровня (сервер)")


class ResultRound(BaseModel):
    """Раунд в итоге: единица раскрывается после матча (карточку отдаёт bff из catalog)."""

    index: int = Field(ge=0, lt=MAX_ROUNDS)
    media_item_id: UUID
    void: bool = Field(default=False, description="Раунд аннулирован (ассет недоступен)")
    results: list[RoundPlayerResult]


class MatchResult(BaseModel):
    """Итог матча (``GET /v1/matches/{id}``)."""

    match_id: UUID
    room_code: RoomCode | None = Field(description="Только участникам матча (не участнику — null)")
    mode: ModeRef
    started_at: datetime
    finished_at: datetime
    finish: MatchFinish
    players: list[ResultPlayer] = Field(max_length=MAX_PLAYERS)
    rounds: list[ResultRound] = Field(max_length=MAX_ROUNDS)
    collection_ids: list[UUID] = Field(default_factory=list)


class MyMatchCard(BaseModel):
    """Матч в «Моих матчах»."""

    match_id: UUID
    mode: ModeRef
    finished_at: datetime
    finish: MatchFinish
    players: int = Field(ge=1, le=MAX_PLAYERS)
    place: int = Field(ge=1, le=MAX_PLAYERS)
    score: int = Field(ge=0)
    outcome: PlayerOutcome | None
    my_finish: PlayerFinish


class MyMatchesPage(BaseModel):
    """Страница моих матчей (keyset-курсор по ``finished_at`` убыв.)."""

    items: list[MyMatchCard]
    next_cursor: str | None = None


# ------------------------------------------------------------------ 4b: очередь, наблюдение
QueueFormat = Literal["classic", "lite"]
QueueState = Literal["searching", "matched"]
LadderKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]


class QueueEnterRequest(_Request):
    """Встать в очередь рейтинговой дуэли (ADR 0038).

    Рейтинг общий на ladder (домен), очередь — отдельная на формат: игрок состоит ровно в
    одной очереди. Шаблон режима выбирает match по ``ladder`` и ``format`` (рейтинговая
    дуэль домена с ``answer.format`` = ``classic`` или ``pick``).
    """

    ladder: LadderKey
    format: QueueFormat


class QueueTicket(BaseModel):
    """Состояние игрока в очереди (ответ ``enter`` и ``tick``)."""

    ladder: LadderKey
    format: QueueFormat
    state: QueueState
    entered_at: datetime
    tick_s: int | None = Field(
        default=None,
        ge=1,
        description="Период heartbeat `POST /queue/tick`, с (`GV_MATCH__TICK_S`)",
    )
    wait_s: int = Field(ge=0, description="Сколько секунд игрок ждёт")
    window: int = Field(ge=0, description="Текущее окно подбора ±, очков (из конфига ladder-а)")
    slow: bool = Field(
        description="Ждёт дольше `slow_hint_after_s`: показать «ищем дольше обычного»"
    )
    room_code: RoomCode | None = Field(
        default=None, description="Комната дуэли, когда `state = matched` (сразу в `countdown`)"
    )


class QueuePoolEntry(BaseModel):
    """Сколько игроков ищут матч в очереди ladder-а и формата."""

    ladder: LadderKey
    format: QueueFormat
    searching: int = Field(ge=0)


class QueuePool(BaseModel):
    """Размер очередей (хаб онлайна); без списка игроков."""

    items: list[QueuePoolEntry]


class SpectateRoomCard(BaseModel):
    """Идущая комната в списке наблюдения (``match.spectate``)."""

    code: RoomCode
    mode: ModeRef
    phase: Phase
    players: int = Field(ge=1, le=MAX_PLAYERS)
    started_at: datetime


class SpectateRoomsPage(BaseModel):
    """Страница комнат для наблюдения (keyset-курсор)."""

    items: list[SpectateRoomCard]
    next_cursor: str | None = None
