"""Переопределения ``clue`` и ``answer.format`` хозяином лобби (ADR 0070).

Что хозяин вправе менять, решает конфиг режима (``session.lobby.overridable``), не код.
Функция только подставляет разрешённое значение в копию тела; совместимость сочетания
(формат с загадкой, источником, состязанием, число уровней) проверяет тот же
``EngineFactory.validate``, что и обычный конфиг, — вызывающий прогоняет по нему результат.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from gv_game_kernel.errors import OverrideNotAllowedError

FIELD_CLUE = "clue"
FIELD_ANSWER_FORMAT = "answer_format"


def _not_allowed(field: str) -> OverrideNotAllowedError:
    return OverrideNotAllowedError(
        "значение настройки не разрешено шаблоном", details={"field": field}
    )


def apply_lobby_overrides(
    body: Mapping[str, Any],  # Any: тело ModeConfig
    *,
    clue_type: str | None = None,
    answer_format: str | None = None,
) -> dict[str, Any]:  # Any: тело ModeConfig
    """Копия тела конфига с переопределениями хозяина лобби.

    Значение, совпадающее с шаблоном, ничего не меняет. Загадка берётся целиком (тип,
    параметры, ревил) из первого варианта ``lobby.overridable.clue`` с этим типом.

    Args:
        body: Тело опубликованного конфига.
        clue_type: Тип загадки или ``None`` — без переопределения.
        answer_format: Формат ответа или ``None``.

    Raises:
        OverrideNotAllowedError: у режима нет лобби или значения нет среди разрешённых
            (``details.field``).
    """
    result: dict[str, Any] = copy.deepcopy(dict(body))  # Any: тело ModeConfig
    over = ((result.get("session") or {}).get("lobby") or {}).get("overridable") or {}
    if clue_type is not None and clue_type != result["clue"]["type"]:
        chosen = next((c for c in over.get("clue") or [] if c.get("type") == clue_type), None)
        if chosen is None:
            raise _not_allowed(FIELD_CLUE)
        result["clue"] = copy.deepcopy(chosen)
    if answer_format is not None and answer_format != result["answer"]["format"]:
        if answer_format not in (over.get("answer_format") or []):
            raise _not_allowed(FIELD_ANSWER_FORMAT)
        result["answer"]["format"] = answer_format
    return result
