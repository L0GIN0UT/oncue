"""Блок экрана: данные одного источника или признак его недоступности.

Страница собирается из независимых блоков: отказ одного сервиса гасит только его блок,
а не всю страницу (деградация из docs/architecture/services.md, карточка bff-web).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BlockStatus(StrEnum):
    """Состояние блока."""

    OK = "ok"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Block[T]:
    """Блок экрана.

    Attributes:
        status: ``ok`` — данные есть; ``unavailable`` — источник не ответил.
        data: Данные блока (только при ``ok``).
    """

    status: BlockStatus
    data: T | None = None

    def __post_init__(self) -> None:
        if (self.status is BlockStatus.OK) != (self.data is not None):
            raise ValueError("данные есть ровно у блока со статусом ok")

    @classmethod
    def ok(cls, data: T) -> Block[T]:
        """Блок с данными."""
        return cls(BlockStatus.OK, data)

    @classmethod
    def unavailable(cls) -> Block[T]:
        """Блок упавшего источника."""
        return cls(BlockStatus.UNAVAILABLE)

    @property
    def is_ok(self) -> bool:
        """Есть ли данные."""
        return self.status is BlockStatus.OK
