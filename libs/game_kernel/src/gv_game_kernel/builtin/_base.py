"""Общие части встроенных плагинов: базовая модель параметров и приведение типов."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic_core import PydanticCustomError


class PluginParams(BaseModel):
    """База моделей ``params``: неизвестные поля запрещены, значения неизменяемы.

    Числа и флаги в наследниках строгие (``StrictInt``/``StrictFloat``/``StrictBool``),
    как в JSON Schema контрактов: ``"5"`` вместо ``5`` — ошибка.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


def typed[P: BaseModel](params: BaseModel, cls: type[P]) -> P:
    """Привести разобранные параметры к модели плагина.

    Raises:
        TypeError: передана модель другого плагина (ошибка сборки движка).
    """
    if not isinstance(params, cls):
        raise TypeError(f"ожидались параметры {cls.__name__}, получены {type(params).__name__}")
    return params


def unique[T](values: Sequence[T]) -> Sequence[T]:
    """Валидатор ``uniqueItems``: повтор значения — ошибка ``schema.unique_items``."""
    if len(set(values)) != len(values):
        raise PydanticCustomError("unique_items", "значения должны быть уникальными")
    return values


def custom_error(code: str, message: str, **ctx: Any) -> PydanticCustomError:
    """Ошибка валидации с машинным типом: в ``ConfigIssue.code`` станет ``schema.<code>``."""
    # Any: контекст сообщения pydantic — произвольные значения для подстановки.
    return PydanticCustomError(code, message, ctx or None)
