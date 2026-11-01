"""Схемы публичного API game (план этапа 2, §2.6) — контракт для фронтенда и bff.

Античит (D11): до ревила в снимке сессии нет ``media_item_id``, названия и
``asset_set_id``; ``reveal`` есть только у завершённого раунда. Этап 3a (план, §2.5):
варианты ответа — непрозрачные ``option_id``, метрика скрытой единицы пары до ответа не
отдаётся. Этап 3b (план, §2.5): сессия по сборнику (``collection_id``, ``rounds``),
режимы сборника и проверка играбельности — без названий единиц (D6).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Domain = Literal["music", "film"]
ModeKey = Annotated[
    str, StringConstraints(pattern=r"^(music|film)\.[a-z0-9_]+\.[a-z0-9_]+$", max_length=100)
]
PresetId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
RoundOutcomeLiteral = Literal["exact", "partial", "miss", "gave_up", "void"]
GuessResultLiteral = Literal["exact", "partial", "miss"]
ClueKind = Literal["audio", "image", "text"]
AnswerFormatLiteral = Literal["classic", "pick"]
Presentation = Literal["score", "streak", "bracket"]
OptionId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,32}$")]
MAX_ROUND_INDEX = 199
"""Последний допустимый номер раунда (``session.rounds`` ≤ 200, план этапа 3a)."""
RoundsChoice = Literal[5, 10, 25, "all"]
"""Число раундов сессии по сборнику (``source.collection.round_options``, ADR 0028)."""
MAX_PLAYABILITY_IDS = 200
"""Сколько единиц черновика проверяет одна ``POST /v1/modes/{key}/playability``."""


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------------------- режимы
class LevelOut(BaseModel):
    """Уровень загадки для UI."""

    index: int = Field(ge=1)
    hint: dict[str, float | int | str | list[str] | None] = Field(
        description=(
            "Что открывает уровень: {seconds} для аудио, {blur_px, crop} для кадра, "
            "{pixel_block} для постера, {frame} для последовательности кадров, "
            "{fields} для описания (накопленные поля), {count} для эмодзи (null — все)"
        )
    )


class PresetOut(BaseModel):
    """Пресет случайного режима."""

    id: str
    title_key: str


class SubjectPick(BaseModel):
    """Выбор персон игроком перед стартом (марафон)."""

    roles: list[Literal["artist", "director", "actor"]]
    max: int = Field(ge=1, le=10, description="Сколько персон можно выбрать")


class ModeStart(BaseModel):
    """Что игрок выбирает перед стартом режима."""

    subject: SubjectPick | None = None


class OnlinePlayersOut(BaseModel):
    """Места комнаты."""

    min: int = Field(ge=2)
    max: int = Field(ge=2)


class OnlineChatOut(BaseModel):
    """Эмоции и готовые фразы (ключи i18n ``match.emotes.*``/``match.phrases.*``) и признак
    свободного чата (только кастомное лобби, ADR 0036)."""

    emotes: list[str]
    phrases: list[str]
    free_text: bool


class OnlineModeOut(BaseModel):
    """Правила комнаты онлайн-режима (``GET /v1/modes?session=online``, ADR 0032)."""

    kind: Literal["duel", "party", "lobby"]
    players: OnlinePlayersOut
    rounds: int = Field(ge=1)
    level_windows_s: list[int] = Field(description="Окно каждого уровня загадки, с")
    contest_type: str = Field(description="Плагин состязания (`race`, `parallel`)")
    ranked: bool
    ladder: str | None = None
    max_collections: int | None = Field(
        default=None, description="Только у лобби по сборнику: сколько сборников можно выбрать"
    )
    # Any: структура `LobbySpec` контракта `mode_config.v1.json` (`overridable` — списки
    # разрешённых значений, в том числе `clue` с параметрами плагина).
    lobby: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Только у кастомного лобби: `{visibility, overridable}` по `$defs/LobbySpec` "
            "контракта конфига режима — из чего выбирает хост"
        ),
    )
    chat: OnlineChatOut


class ModeOut(BaseModel):
    """Опубликованный режим (``GET /v1/modes``)."""

    key: str
    version: int
    domain: Domain
    title_key: str
    source_type: str
    clue_type: str
    rounds: int
    levels: list[LevelOut]
    allow_guest: bool
    presets: list[PresetOut] | None = None
    answer_format: AnswerFormatLiteral = "classic"
    presentation: Presentation = "score"
    start: ModeStart | None = Field(default=None, description="null — выбирать нечего")
    round_options: list[RoundsChoice] | None = Field(
        default=None,
        description=(
            "Только у шаблона сборника (`kind=collection`): варианты числа раундов из "
            "конфига, без учёта конкретного сборника"
        ),
    )
    min_items: int | None = Field(
        default=None,
        ge=0,
        description="Только у шаблона сборника: минимум играбельных единиц для игры",
    )
    online: OnlineModeOut | None = Field(
        default=None,
        description="Только у онлайн-режима (`session=online`); у соло-режимов — null",
    )


class SubjectOut(BaseModel):
    """Персона для марафона (``GET /v1/modes/{key}/subjects``)."""

    subject_id: UUID
    name: str
    role: Literal["artist", "director", "actor"]
    items: int = Field(ge=0, description="Сколько играбельных единиц у персоны")


# ----------------------------------------------------------------------------- сессия
class StartSessionRequest(_Request):
    """Старт сессии режима."""

    mode_key: ModeKey
    preset_id: PresetId | None = None
    subject_ids: list[UUID] | None = Field(
        default=None, min_length=1, max_length=10, description="Персоны марафона"
    )
    collection_id: UUID | None = Field(
        default=None,
        description="Сборник для режима с source `collection` (опубликованный, не скрытый)",
    )
    rounds: RoundsChoice | None = Field(
        default=None,
        description="Число раундов из `round_options` режима сборника; null — первый вариант",
    )


class ModeRef(BaseModel):
    """Режим сессии (версия фиксируется при старте)."""

    key: str
    version: int
    domain: Domain
    clue_type: str


class GuessOut(BaseModel):
    """Ход в истории раунда."""

    kind: Literal["item", "text", "option", "skip"]
    display: str
    result: GuessResultLiteral | None = Field(description="null у пропуска")


class PersonOut(BaseModel):
    """Персона в ревиле."""

    name: str
    role: str


class FinalClue(BaseModel):
    """Полный ассет для ревила."""

    kind: ClueKind
    url: str
    expires_at: datetime
    mime: str


class Artwork(BaseModel):
    """Картинка ответа: обложка альбома трека или постер фильма (ADR 0073)."""

    url: str
    expires_at: datetime
    mime: str


class Reveal(BaseModel):
    """Ответ раунда — только у завершённого раунда."""

    media_item_id: UUID
    title: str
    year: int | None
    persons: list[PersonOut]
    metric: float | None = Field(
        default=None, description="Метрика сравнения загаданной единицы («больше/меньше»)"
    )
    final_clue: FinalClue
    artwork: Artwork | None = Field(
        default=None,
        description=(
            "Обложка (трек) или постер (фильм); null, если картинки нет или она временно "
            "недоступна - заглушку рисует клиент"
        ),
    )


class ShownItem(BaseModel):
    """Единица, которую игрок видит открыто (опорная единица пары, вариант сетки)."""

    option_id: OptionId | None = None
    title: str
    year: int | None = None
    metric: float | None = Field(default=None, description="Только у опорной единицы пары")
    clue: FinalClue | None = Field(default=None, description="Кадр или отрывок варианта")


class OptionOut(BaseModel):
    """Вариант ответа формата ``pick``."""

    option_id: OptionId
    label_key: str | None = None
    title: str | None = None


class RoundProgress(BaseModel):
    """Прогресс сессии для UI: жизни, серия, тур сетки."""

    lives_left: int | None = None
    streak: int | None = None
    stage: int | None = Field(default=None, description="Размер тура сетки: 32, 16 … 2")


class RoundOut(BaseModel):
    """Раунд в снимке сессии."""

    index: int = Field(ge=0)
    status: Literal["pending", "active", "finished", "void"]
    level: int = Field(ge=1, description="Текущий открытый уровень")
    levels_count: int = Field(ge=1)
    guesses: list[GuessOut]
    points: int | None = None
    reveal: Reveal | None = Field(default=None, description="Только у завершённого раунда")
    shown: list[ShownItem] = Field(default_factory=list)
    options: list[OptionOut] = Field(default_factory=list)
    progress: RoundProgress | None = None
    subjects: list[PersonOut] = Field(
        default_factory=list,
        description=(
            "Персоны, задающие область ответа раунда (сборник одного исполнителя, марафон): "
            "ответом считается название, имя персоны не принимается, автоподсказка ищет только "
            "по названиям этих персон. Пусто - область не ограничена. Необязательное поле"
        ),
    )


class SessionSnapshot(BaseModel):
    """Снимок сессии; ``version`` — для ``expected_version`` следующего хода."""

    id: UUID
    version: int
    status: Literal["active", "finished", "abandoned"]
    mode: ModeRef
    day: date | None = None
    score: int
    max_score: int
    current_round: int
    rounds: list[RoundOut]


# ----------------------------------------------------------------------------- ассеты
class ClueRequest(_Request):
    """Запрос ассета уровня (уровень не выше открытого)."""

    level: int = Field(ge=1, le=20)


class ClueMeta(BaseModel):
    """Параметры уровня для UI — поля ``hint_fields`` плагина загадки ядра.

    Набор полей — объединение ``hint_fields`` встроенных загадок; у конкретной загадки
    заполнены только её поля, остальные ``null``.
    """

    seconds: float | None = Field(default=None, description="audio_intro: длина отрывка, с")
    blur_px: float | None = Field(default=None, description="frame_progressive: размытие, px")
    crop: float | None = Field(
        default=None, description="frame_progressive: видимая доля кадра (0..1]"
    )
    pixel_block: int | None = Field(
        default=None, description="poster_masked: размер блока пикселизации, px"
    )
    frame: int | None = Field(default=None, description="frame_sequence: позиция кадра")
    fields: list[str] | None = Field(
        default=None, description="description: поля, открытые на уровне"
    )
    count: int | None = Field(
        default=None, description="emoji: сколько эмодзи открыто (null у meta — все)"
    )
    blur: float | None = Field(
        default=None,
        deprecated=True,
        description="Устарело, всегда null: размытие передаётся в blur_px",
    )


class ClueOut(BaseModel):
    """Короткоживущая ссылка на ассет уровня (срок - `expires_at`; TTL задаёт media-pipeline)."""

    level: int
    kind: ClueKind
    url: str
    expires_at: datetime
    mime: str
    meta: ClueMeta


# ----------------------------------------------------------------------------- ходы
class ItemAnswer(_Request):
    """Ответ выбором из автокомплита."""

    media_item_id: UUID


class TextAnswer(_Request):
    """Ответ свободным текстом."""

    text: str = Field(min_length=1, max_length=120)


class OptionAnswer(_Request):
    """Ответ выбором варианта раунда (формат ``pick``)."""

    option_id: OptionId


class GuessRequest(_Request):
    """Ответ на текущем уровне раунда."""

    round_index: int = Field(ge=0, le=MAX_ROUND_INDEX)
    expected_version: int = Field(ge=0)
    answer: ItemAnswer | TextAnswer | OptionAnswer


class SkipRequest(_Request):
    """Пропуск уровня."""

    round_index: int = Field(ge=0, le=MAX_ROUND_INDEX)
    expected_version: int = Field(ge=0)


class GiveUpRequest(_Request):
    """Сдаться в раунде."""

    expected_version: int = Field(ge=0)


class MoveStats(BaseModel):
    """Статистика выбора (сетка): сколько игроков выбрали так же."""

    agree_pct: float = Field(ge=0, le=100)


class MoveResult(BaseModel):
    """Итог хода."""

    result: GuessResultLiteral | None = Field(
        description="Результат ответа; null для пропуска и сдачи"
    )
    round: RoundOut
    session: SessionSnapshot
    stats: MoveStats | None = None


# ----------------------------------------------------------------------------- итог
class ResultRound(BaseModel):
    """Раунд в итоге сессии."""

    index: int
    outcome: RoundOutcomeLiteral
    level: int | None
    points: int
    reveal: Reveal | None = Field(description="null у раунда void (ассет недоступен)")


class ShareOut(BaseModel):
    """Текст-сетка для «Поделиться» (D14)."""

    text: str


class DailyRoundStat(BaseModel):
    """Доля угадавших раунд дейлика (только пользователи)."""

    index: int
    exact_pct: float = Field(ge=0, le=100)


class RecordOut(BaseModel):
    """Личный рекорд режима."""

    mode_key: str
    best: int
    percentile: float | None = Field(default=None, ge=0, le=100)
    achieved_at: datetime


class ResultRecord(BaseModel):
    """Рекорд в итоге сессии (только пользователи)."""

    best: int
    is_new: bool
    percentile: float | None = Field(default=None, ge=0, le=100)


class BracketPick(BaseModel):
    """Выбор в сетке."""

    stage: int
    winner: str
    loser: str


class BracketOut(BaseModel):
    """Итог сетки: чемпион и выборы по турам."""

    champion: str
    picks: list[BracketPick]


class CollectionRef(BaseModel):
    """Сборник сессии: версия состава фиксируется при старте (ADR 0028)."""

    id: UUID
    version: int = Field(ge=1, description="content_version сборника")


class SessionResult(BaseModel):
    """Итог завершённой сессии."""

    score: int
    max_score: int
    rounds: list[ResultRound]
    share: ShareOut
    daily_stats: list[DailyRoundStat] | None = None
    presentation: Presentation = "score"
    record: ResultRecord | None = None
    bracket: BracketOut | None = None
    collection: CollectionRef | None = Field(default=None, description="Сессия по сборнику")
    mode_key: str | None = Field(default=None, description="Режим сессии")
    domain: Domain | None = Field(default=None, description="Домен режима")
    day: date | None = Field(default=None, description="Период планового набора; иначе `null`")
    daily: bool = Field(
        default=False,
        description=(
            "Сессия дейлика: плановый набор с типом Source из "
            "`GV_GAME__DAILY_RESULTS_SOURCE_TYPES` (результаты друзей считаются только для неё)"
        ),
    )


# ----------------------------------------------------------------------------- сборники
class CollectionModeOut(BaseModel):
    """Режим сборника (``GET /v1/collections/{id}/modes``): сколько единиц играбельно."""

    mode_key: str
    title_key: str
    clue_type: str
    allow_guest: bool
    playable: int = Field(ge=0, description="Играбельных единиц сборника в этом режиме")
    total: int = Field(ge=0, description="Единиц в сборнике (активных в каталоге)")
    available: bool = Field(description="playable ≥ min_items режима")
    round_options: list[RoundsChoice] = Field(
        description="Варианты числа раундов, доступные при текущем playable"
    )


class PlayabilityRequest(_Request):
    """Единицы черновика сборника для проверки играбельности в режиме."""

    media_item_ids: list[UUID] = Field(min_length=1, max_length=MAX_PLAYABILITY_IDS)


class PlayabilityOut(BaseModel):
    """Какие из переданных единиц играбельны в режиме (без названий и ассетов, D6)."""

    mode_key: str
    playable_ids: list[UUID]


class DailyStatus(BaseModel):
    """Статус дейлика для текущего субъекта (гость или пользователь)."""

    mode_key: str
    day: date
    status: Literal["not_started", "in_progress", "finished"]
    session_id: UUID | None = None
    score: int | None = None
    max_score: int
    next_reset_at: datetime
