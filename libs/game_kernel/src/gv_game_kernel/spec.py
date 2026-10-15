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


class SessionSpec(_Spec):
    """Правила сессии.

    Attributes:
        kind: Вид сессии (этап 2 — ``solo``).
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
        session: Правила сессии.
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
    session: SessionSpec
    rewards: JsonObject = Field(default_factory=dict)
    features: dict[str, StrictBool] = Field(default_factory=dict)


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


def parse_mode_config(body: Mapping[str, Any]) -> ModeConfig:
    """Разобрать тело конфига; ошибки схемы — ``ConfigError`` со списком путей.

    Raises:
        ConfigError: ``details["errors"] = [{"path": "session.rounds", "code": "schema.<тип>"}]``.
    """
    try:
        return ModeConfig.model_validate(dict(body))
    except ValidationError as exc:
        errors = [
            {"path": error_path(tuple(e["loc"])), "code": f"schema.{e['type']}"}
            for e in exc.errors(include_url=False, include_input=False)
        ]
        raise ConfigError(details={"errors": errors}) from exc
