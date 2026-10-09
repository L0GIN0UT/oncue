"""Матрица ролей: роль → права. Решения о доступе принимаются по правам (``perms``)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from gv_core.errors import InvalidInputError

ROLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PERM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
DEFAULT_ROLE = "user"
"""Роль, которую получает каждый новый пользователь (без прав)."""


@dataclass(frozen=True, slots=True)
class RoleMatrix:
    """Снимок матрицы ролей.

    Attributes:
        roles: Роль → набор прав.
        permissions: Справочник существующих прав (сид миграции); новое право появляется
            только миграцией вместе с кодом, который его проверяет.
    """

    roles: Mapping[str, frozenset[str]]
    permissions: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "roles", MappingProxyType(dict(self.roles)))
        for role, perms in self.roles.items():
            if not ROLE_NAME_RE.fullmatch(role):
                raise InvalidInputError(
                    "недопустимое имя роли", code="roles.invalid_name", details={"role": role}
                )
            unknown = sorted(perms - self.permissions)
            if unknown:
                raise InvalidInputError(
                    "неизвестные права",
                    code="roles.unknown_permission",
                    details={"role": role, "perms": unknown},
                )

    def __deepcopy__(self, memo: dict[int, object]) -> RoleMatrix:
        return self  # неизменяемый снимок

    def require_known(self, roles: Iterable[str]) -> frozenset[str]:
        """Проверить, что роли существуют.

        Raises:
            InvalidInputError: ``roles.unknown_role``.
        """
        result = frozenset(roles)
        unknown = sorted(result - self.roles.keys())
        if unknown:
            raise InvalidInputError(
                "неизвестные роли", code="roles.unknown_role", details={"roles": unknown}
            )
        return result

    def effective_perms(self, roles: Iterable[str]) -> frozenset[str]:
        """Объединение прав ролей (неизвестные роли игнорируются)."""
        result: set[str] = set()
        for role in roles:
            result |= self.roles.get(role, frozenset())
        return frozenset(result)

    def can_grant(self, actor_perms: Iterable[str], roles: Iterable[str]) -> bool:
        """Можно ли выдать роли: их права не шире прав того, кто выдаёт (нет эскалации)."""
        return self.effective_perms(roles) <= frozenset(actor_perms)

    def replace(self, roles: Mapping[str, Iterable[str]]) -> RoleMatrix:
        """Новая матрица с тем же справочником прав (валидируется в конструкторе)."""
        return RoleMatrix(
            roles={name: frozenset(perms) for name, perms in roles.items()},
            permissions=self.permissions,
        )
