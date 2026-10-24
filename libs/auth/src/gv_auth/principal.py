"""Субъект запроса — кто действует и что ему разрешено."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntFlag, StrEnum
from uuid import UUID


class Sanction(IntFlag):
    """Ограничения пользователя (битовая маска в claim ``sanctions``)."""

    NONE = 0
    RANKED = 1  # нет рейтинговых игр
    CUSTOM_CONTENT = 2  # свои картинки и статус скрыты
    CHAT_SLOW = 4  # медленный чат
    CHAT_MUTE = 8  # чат выключен
    COMMENTS = 16  # комментарии выключены
    ONLINE = 32  # нет онлайна: комнаты, лобби, очереди, приглашения (ADR 0052)


class SubjectKind(StrEnum):
    """Вид субъекта пользовательского токена."""

    USER = "user"
    GUEST = "guest"


@dataclass(frozen=True, slots=True)
class Principal:
    """Аутентифицированный субъект: пользователь или гость (D5 плана этапа 2).

    Решения о доступе принимаются по правам (``perms``), а не по ролям: добавление роли
    не требует правок кода. Роли — только для отображения и аудита.

    У гостя ``user_id`` — идентификатор гостевой сессии identity (uuid7 из ``sub``), прав
    нет; ручки, которым нужен аккаунт, закрываются ``require_user``.
    """

    user_id: UUID
    roles: frozenset[str] = field(default_factory=frozenset)
    perms: frozenset[str] = field(default_factory=frozenset)
    sanctions: Sanction = Sanction.NONE
    plan: str | None = None
    token_id: str | None = None
    session_id: str | None = None
    """Сессия identity (claim ``sid``): по ней отзываются выданные access-токены."""
    is_guest: bool = False
    """Гостевой токен (claim ``gst = true``)."""

    @property
    def subject_kind(self) -> SubjectKind:
        """``user`` или ``guest``."""
        return SubjectKind.GUEST if self.is_guest else SubjectKind.USER

    def has_perm(self, perm: str) -> bool:
        """Есть ли право ``perm``."""
        return perm in self.perms

    def has_all(self, *perms: str) -> bool:
        """Есть ли все перечисленные права."""
        return all(p in self.perms for p in perms)

    def is_sanctioned(self, sanction: Sanction) -> bool:
        """Действует ли ограничение."""
        return bool(self.sanctions & sanction)
