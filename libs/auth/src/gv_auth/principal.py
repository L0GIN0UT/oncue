"""Субъект запроса — кто действует и что ему разрешено."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntFlag
from uuid import UUID


class Sanction(IntFlag):
    """Ограничения пользователя (битовая маска в claim ``sanctions``)."""

    NONE = 0
    RANKED = 1  # нет рейтинговых игр
    CUSTOM_CONTENT = 2  # свои картинки и статус скрыты
    CHAT_SLOW = 4  # медленный чат
    CHAT_MUTE = 8  # чат выключен
    COMMENTS = 16  # комментарии выключены


@dataclass(frozen=True, slots=True)
class Principal:
    """Аутентифицированный пользователь.

    Решения о доступе принимаются по правам (``perms``), а не по ролям: добавление роли
    не требует правок кода. Роли — только для отображения и аудита.
    """

    user_id: UUID
    roles: frozenset[str] = field(default_factory=frozenset)
    perms: frozenset[str] = field(default_factory=frozenset)
    sanctions: Sanction = Sanction.NONE
    plan: str | None = None
    token_id: str | None = None

    def has_perm(self, perm: str) -> bool:
        """Есть ли право ``perm``."""
        return perm in self.perms

    def has_all(self, *perms: str) -> bool:
        """Есть ли все перечисленные права."""
        return all(p in self.perms for p in perms)

    def is_sanctioned(self, sanction: Sanction) -> bool:
        """Действует ли ограничение."""
        return bool(self.sanctions & sanction)
