"""Политика паролей и обязательные согласия."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from gv_core.errors import InvalidInputError


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    """Требования к паролю (docs/plans/stage-1.md §4).

    Attributes:
        min_length: Минимальная длина в символах.
        max_length: Максимальная длина — ограничивает стоимость хэширования (защита от DoS).
        banned: Распространённые пароли в нижнем регистре (топ-10k).
    """

    min_length: int = 10
    max_length: int = 128
    banned: frozenset[str] = field(default_factory=frozenset)

    def check(self, password: str, *, nickname: str, email: str) -> None:
        """Проверить пароль.

        Raises:
            InvalidInputError: ``auth.password_weak`` с ``details.reason``.
        """
        if len(password) < self.min_length:
            self._fail("too_short")
        if len(password) > self.max_length:
            self._fail("too_long")
        lowered = password.lower()
        if lowered in self.banned:
            self._fail("too_common")
        local_part = email.split("@", 1)[0].lower()
        if lowered in {nickname.lower(), email.lower(), local_part}:
            self._fail("matches_login")

    @staticmethod
    def _fail(reason: str) -> None:
        raise InvalidInputError(
            "пароль не подходит", code="auth.password_weak", details={"reason": reason}
        )


@dataclass(frozen=True, slots=True, order=True)
class ConsentRequirement:
    """Согласие определённой редакции: ``terms:2026-10``."""

    kind: str
    version: str


def parse_consent_requirements(raw: str) -> tuple[ConsentRequirement, ...]:
    """Разобрать настройку ``kind:version,kind:version``.

    Raises:
        ValueError: элемент без версии (ошибка конфигурации — процесс не должен стартовать).
    """
    result: list[ConsentRequirement] = []
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        kind, sep, version = item.partition(":")
        if not sep or not kind.strip() or not version.strip():
            raise ValueError(f"согласие должно быть вида kind:version, получено {item!r}")
        result.append(ConsentRequirement(kind.strip(), version.strip()))
    return tuple(result)


def missing_consents(
    required: Iterable[ConsentRequirement], granted: Iterable[ConsentRequirement]
) -> tuple[ConsentRequirement, ...]:
    """Обязательные согласия, которых нет среди выданных (с точностью до редакции)."""
    have = set(granted)
    return tuple(r for r in required if r not in have)
