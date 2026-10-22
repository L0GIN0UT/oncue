"""Модели конфига режима ``ModeConfig`` строго по ``contracts/modes/mode_config.v1.json``.

Меняет только шаг 0 плана (контракт первичен): соответствие моделей схеме проверяет
contract-тест ``tests/test_contracts.py``. ``params`` плагинов здесь — словари: их
валидирует ``params_schema`` конкретного плагина (``EngineFactory.validate``).

``version`` и ``status`` в теле конфига нет — их задаёт сервис game при публикации.
Числа и флаги строгие (``StrictInt``/``StrictBool``): ``"5"`` вместо ``5`` или ``"true"``
вместо ``true`` — ошибка, как и в JSON Schema; неизвестные поля запрещены.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationError,
)

from gv_game_kernel.errors import ConfigError

# Any: значения params и rewards произвольны — их проверяет плагин (или этап 5 для rewards).
type JsonObject = dict[str, Any]

Domain = Literal["music", "film"]
PluginKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
I18nKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$", max_length=200)]
ModeKey = Annotated[
    str, StringConstraints(pattern=r"^(music|film)\.[a-z0-9_]+\.[a-z0-9_]+$", max_length=100)
]


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PluginSpec(_Spec):
    """Ссылка на плагин из реестра: ключ и его параметры.

    Attributes:
        type: Ключ плагина в ``PluginRegistry``.
        params: Параметры; схема — ``params_schema`` плагина.
    """

    type: PluginKey
    params: JsonObject


class SourceSpec(PluginSpec):
    """Источник единиц раундов (плагин ``Source``)."""


class ClueSpec(PluginSpec):
    """Тип загадки и её уровни (плагин ``ClueType``).

    ``params`` совпадают с параметрами профиля media-pipeline: по их ``params_hash`` game
    находит готовые ассеты (D7).

    Attributes:
        reveal: Подача ревила другим типом загадки того же домена (ADR 0026), например
            кадр фильма после ``audio_blackout``; ``None`` — последний уровень самой загадки.
    """

    reveal: PluginSpec | None = None


class MatcherSpec(PluginSpec):
    """Сверка ответа (плагин ``AnswerMatcher``)."""


class ScoringSpec(PluginSpec):
    """Подсчёт очков (плагин ``Scorer``); очки уровней — в ``params.points`` (D7)."""


class AnswerSpec(_Spec):
    """Формат ответа, matcher и виды частичного ответа.

    Attributes:
        format: Ключ плагина ``AnswerFormat``: ``classic`` — ввод названия, ``pick`` — выбор
            варианта раунда (ADR 0023).
        matcher: Плагин сверки.
        partial: Ключи плагинов ``PartialRule`` (например ``artist``).
    """

    format: PluginKey
    matcher: MatcherSpec
    partial: tuple[PluginKey, ...] = Field(default=(), max_length=10)


class SoloSessionSpec(_Spec):
    """Правила соло-сессии (играет сервис game).

    Attributes:
        kind: Вид сессии — ``solo``.
        rounds: Число раундов.
        attempts_per_level: Ответов на один уровень загадки (D9: 1 в соло).
        skip_allowed: Можно ли пропустить уровень.
        give_up_allowed: Можно ли сдаться в раунде.
        allow_guest: Пускать ли гостей (D5).
        abandon_after_min: Через сколько минут без ходов сессия брошена.
        end: Правило досрочного завершения (плагин ``EndRule``, ADR 0023); ``None`` —
            сессия идёт все ``rounds`` раундов. С правилом ``rounds`` — максимум раундов.
    """

    kind: Literal["solo"]
    rounds: StrictInt = Field(ge=1, le=200)
    attempts_per_level: StrictInt = Field(ge=1, le=5)
    skip_allowed: StrictBool
    give_up_allowed: StrictBool
    allow_guest: StrictBool
    abandon_after_min: StrictInt = Field(ge=1, le=10080)
    end: PluginSpec | None = None


SessionSpec = SoloSessionSpec
"""Прежнее имя модели соло-сессии (этапы 2–3); новый код использует ``SoloSessionSpec``."""

OnlineKind = Literal["duel", "party", "lobby"]
Visibility = Literal["public", "private"]
LadderKey = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$", max_length=64)
]
MAX_ONLINE_PLAYERS = 6
"""Потолок игроков онлайн-комнаты по контракту (лобби до 32 — после релиза, §14 плана)."""


class PlayersSpec(_Spec):
    """Число игроков комнаты (ADR 0032).

    Attributes:
        min: Минимум для старта матча.
        max: Максимум мест в комнате.
    """

    min: StrictInt = Field(ge=2, le=MAX_ONLINE_PLAYERS)
    max: StrictInt = Field(ge=2, le=MAX_ONLINE_PLAYERS)


class ChatSpec(_Spec):
    """Эмоции, готовые фразы и свободный чат комнаты (D14, ADR 0036).

    Attributes:
        emotes: Ключи i18n эмоций (доступны всем режимам).
        phrases: Ключи i18n готовых фраз.
        free_text: Свободный чат; допустим только у ``kind: lobby`` (проверка ядра).
    """

    emotes: tuple[I18nKey, ...] = Field(default=(), max_length=32)
    phrases: tuple[I18nKey, ...] = Field(default=(), max_length=32)
    free_text: StrictBool = False


class LobbyOverridable(_Spec):
    """Что хост кастомного лобби может выбрать вместо значений шаблона (D12).

    Каждое поле — список разрешённых значений; значение шаблона обязано входить в список
    (перекрёстная проверка ядра). Пустой список или ``None`` — поле не переопределяется.

    Attributes:
        rounds: Варианты числа раундов.
        level_window_s: Варианты окна уровня в секундах (одно значение на все уровни).
        answer_format: Варианты формата ответа (ключи ``AnswerFormat``).
        clue: Варианты загадки (тип и параметры — как ``clue`` конфига).
        players_max: Варианты числа мест.
        visibility: Варианты видимости комнаты.
    """

    rounds: tuple[Annotated[StrictInt, Field(ge=1, le=50)], ...] | None = Field(
        default=None, max_length=10
    )
    level_window_s: tuple[Annotated[StrictInt, Field(ge=3, le=300)], ...] | None = Field(
        default=None, max_length=10
    )
    answer_format: tuple[PluginKey, ...] | None = Field(default=None, max_length=5)
    clue: tuple[ClueSpec, ...] | None = Field(default=None, max_length=10)
    players_max: tuple[Annotated[StrictInt, Field(ge=2, le=MAX_ONLINE_PLAYERS)], ...] | None = (
        Field(default=None, max_length=5)
    )
    visibility: tuple[Visibility, ...] | None = Field(default=None, max_length=2)


class LobbySpec(_Spec):
    """Настройки шаблона кастомного лобби (D12).

    Attributes:
        overridable: Что может менять хост.
        visibility: Видимость по умолчанию (открытые лобби видны в списке).
    """

    overridable: LobbyOverridable
    visibility: Visibility = "private"


class OnlineSessionSpec(_Spec):
    """Правила онлайн-сессии (играет сервис match; ADR 0032, D2).

    Attributes:
        kind: ``duel`` (1×1), ``party`` (3–6) или ``lobby`` (кастомное лобби по шаблону).
        players: Минимум и максимум игроков.
        rounds: Число раундов матча.
        countdown_s: Отсчёт перед первым раундом.
        level_windows_s: Окно каждого уровня загадки в секундах; длина равна числу
            уровней (перекрёстная проверка ядра).
        reveal_s: Длительность ревила между раундами.
        contest: Правило состязания (плагин ``ContestRule``: ``race``, ``parallel``).
        disconnect_grace_s: Сколько ждать отключившегося игрока до выбывания (D10).
        ranked: Рейтинговый матч (только из очереди подбора, этап 4b).
        ladder: Ключ ladder-а rating (обязателен при ``ranked``).
        lobby: Настройки кастомного лобби (обязательны у ``kind: lobby``).
        chat: Эмоции, фразы и свободный чат.
    """

    kind: OnlineKind
    players: PlayersSpec
    rounds: StrictInt = Field(ge=1, le=50)
    countdown_s: StrictInt = Field(ge=0, le=30)
    level_windows_s: tuple[Annotated[StrictInt, Field(ge=3, le=300)], ...] = Field(
        min_length=1, max_length=20
    )
    reveal_s: StrictInt = Field(ge=0, le=60)
    contest: PluginSpec
    disconnect_grace_s: StrictInt = Field(ge=5, le=300)
    ranked: StrictBool = False
    ladder: LadderKey | None = None
    lobby: LobbySpec | None = None
    chat: ChatSpec = Field(default_factory=ChatSpec)


AnySessionSpec = Annotated[SoloSessionSpec | OnlineSessionSpec, Field(discriminator="kind")]
"""Сессия режима: соло (game) или онлайн (match) — по ``kind``."""


class ModeConfig(_Spec):
    """Тело конфига режима (без ``version`` и ``status``).

    Attributes:
        key: ``domain.area.name``, например ``music.daily.classic``.
        title_i18n: Ключ i18n названия.
        domain: Домен контента.
        description_i18n: Ключ i18n описания.
        source: Источник единиц.
        clue: Тип загадки.
        answer: Ответ и matcher.
        scoring: Подсчёт очков.
        session: Правила сессии: соло (game) или онлайн (match), по ``kind``.
        rewards: Награды (без проверки до этапа 5).
        features: Продуктовые флаги режима.
    """

    key: ModeKey
    title_i18n: I18nKey
    domain: Domain
    description_i18n: I18nKey | None = None
    source: SourceSpec
    clue: ClueSpec
    answer: AnswerSpec
    scoring: ScoringSpec
    session: AnySessionSpec
    rewards: JsonObject = Field(default_factory=dict)
    features: dict[str, StrictBool] = Field(default_factory=dict)

    @property
    def is_online(self) -> bool:
        """Онлайн-режим (играет match, ADR 0032)."""
        return isinstance(self.session, OnlineSessionSpec)


def solo_end(config: ModeConfig) -> PluginSpec | None:
    """Правило досрочного завершения соло-сессии; у онлайн-сессии его нет (``None``)."""
    session = config.session
    return session.end if isinstance(session, SoloSessionSpec) else None


def error_path(loc: tuple[int | str, ...]) -> str:
    """JSON-путь поля из ``loc`` ошибки pydantic: ``("session", "rounds")`` → ``session.rounds``.

    Индексы списков — ``[i]``: ``answer.partial[0]``. Пустой ``loc`` — корень ``$``.
    """
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path or "$"


_SESSION_TAGS = frozenset({"solo", "duel", "party", "lobby"})


def _strip_session_tag(loc: tuple[int | str, ...]) -> tuple[int | str, ...]:
    """Убрать тег дискриминатора из пути: ``session.solo.rounds`` → ``session.rounds``.

    pydantic вставляет ``kind`` ветки union в ``loc``; путь ошибки для автора конфига и
    админки не должен зависеть от устройства модели.
    """
    if loc[:1] == ("session",) and loc[1:2] and loc[1] in _SESSION_TAGS:
        return (loc[0], *loc[2:])
    return loc


def parse_mode_config(body: Mapping[str, Any]) -> ModeConfig:
    """Разобрать тело конфига; ошибки схемы — ``ConfigError`` со списком путей.

    Raises:
        ConfigError: ``details["errors"] = [{"path": "session.rounds", "code": "schema.<тип>"}]``.
    """
    try:
        return ModeConfig.model_validate(dict(body))
    except ValidationError as exc:
        errors = [
            {"path": error_path(_strip_session_tag(tuple(e["loc"]))), "code": f"schema.{e['type']}"}
            for e in exc.errors(include_url=False, include_input=False)
        ]
        raise ConfigError(details={"errors": errors}) from exc
