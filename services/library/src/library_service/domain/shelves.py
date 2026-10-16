"""Полки медиатеки: конфиг системных полок и ссылки на полку (D11, ADR 0031).

Набор системных полок, их домены и правило ``exclusive_with`` задаёт конфиг
(``GV_LIBRARY__SHELVES_CONFIG``, по умолчанию ``config/shelves.yaml`` пакета) — в коде нет
ни одного ключа полки. Модуль — чистый Python: файл читает инфраструктура и передаёт сюда
уже разобранный YAML.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from library_service.domain.errors import ShelfNotFoundError

CATALOG_DOMAINS: Final = ("music", "film")
"""Домены единиц по контракту событий catalog (``MediaItemCreated.domain``)."""
SYSTEM_PREFIX: Final = "system:"
KEY_PATTERN: Final = r"[a-z][a-z0-9_]{0,63}"
_KEY_RE = re.compile(KEY_PATTERN)
# Только каноническая запись uuid (нижний регистр, с дефисами): у полки одна ссылка.
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_I18N_KEY_RE = re.compile(r"[a-z][a-z0-9_.]{0,127}")


class ShelfKind(StrEnum):
    """Вид полки: системная (из конфига) или своя."""

    SYSTEM = "system"
    CUSTOM = "custom"


class SaveSource(StrEnum):
    """Экран, с которого сохранили или убрали единицу (поле ``source`` событий)."""

    REVEAL = "reveal"
    RESULTS = "results"
    CARD = "card"
    COLLECTION = "collection"
    LIBRARY = "library"


class RemoveSource(StrEnum):
    """Причина удаления единицы с полки: экран (как ``SaveSource``) или правило."""

    REVEAL = "reveal"
    RESULTS = "results"
    CARD = "card"
    COLLECTION = "collection"
    LIBRARY = "library"
    EXCLUSIVE = "exclusive"
    """Убрана правилом ``exclusive_with`` при сохранении на другую системную полку."""
    SHELF_DELETED = "shelf_deleted"
    """Убрана вместе со своей полкой."""


class ShelvesConfigError(ValueError):
    """Конфиг системных полок не прошёл проверку (ошибка развёртывания, не клиента)."""


@dataclass(frozen=True, slots=True)
class SystemShelfSpec:
    """Системная полка из конфига.

    Attributes:
        key: Ключ полки (``watch_later``); в ссылке — ``system:<key>``.
        title_key: Ключ i18n названия (переводит фронтенд).
        domains: Домены единиц, которые можно сохранить на полку.
        exclusive_with: Ключи системных полок, откуда единица уходит при сохранении сюда.
    """

    key: str
    title_key: str
    domains: tuple[str, ...]
    exclusive_with: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CardSpec:
    """Как строится карточка единицы на полке.

    Attributes:
        person_roles: Роли персон карточки в порядке приоритета (``artist``, ``director``).
        persons_max: Сколько персон показать.
    """

    person_roles: tuple[str, ...]
    persons_max: int


@dataclass(frozen=True, slots=True)
class ShelfCatalog:
    """Разобранный конфиг полок.

    Attributes:
        system: Системные полки в порядке показа.
        custom_domains: Домены своих полок.
        card: Карточка единицы.
    """

    system: tuple[SystemShelfSpec, ...]
    custom_domains: tuple[str, ...]
    card: CardSpec

    def spec(self, key: str) -> SystemShelfSpec:
        """Системная полка по ключу.

        Raises:
            ShelfNotFoundError: ключа нет в конфиге.
        """
        for spec in self.system:
            if spec.key == key:
                return spec
        raise ShelfNotFoundError

    def find(self, key: str) -> SystemShelfSpec | None:
        """Системная полка по ключу или ``None``."""
        return next((s for s in self.system if s.key == key), None)

    @classmethod
    def from_mapping(cls, data: object, *, domains: Sequence[str]) -> ShelfCatalog:
        """Разобрать конфиг (результат ``yaml.safe_load``).

        Args:
            data: Корень конфига.
            domains: Допустимые домены единиц (контракт событий catalog).

        Raises:
            ShelvesConfigError: структура, ключи, домены или ``exclusive_with`` неверны.
        """
        root = _mapping(data, "корень")
        if root.get("version") != 1:
            raise ShelvesConfigError("version: ожидается 1")
        allowed = tuple(domains)
        raw_system = root.get("system")
        if not isinstance(raw_system, list) or not raw_system:
            raise ShelvesConfigError("system: нужен непустой список полок")
        specs = tuple(_system_spec(item, i, allowed) for i, item in enumerate(raw_system))
        keys = [s.key for s in specs]
        if len(set(keys)) != len(keys):
            raise ShelvesConfigError("system: ключи полок повторяются")
        for spec in specs:
            for other in spec.exclusive_with:
                if other == spec.key or other not in keys:
                    raise ShelvesConfigError(
                        f"system.{spec.key}.exclusive_with: неизвестная полка {other!r}"
                    )
        custom = _mapping(root.get("custom"), "custom")
        card = _mapping(root.get("card"), "card")
        persons_max = card.get("persons_max")
        if not isinstance(persons_max, int) or isinstance(persons_max, bool) or persons_max < 0:
            raise ShelvesConfigError("card.persons_max: целое ≥ 0")
        return cls(
            system=specs,
            custom_domains=_domains(custom.get("domains"), "custom.domains", allowed),
            card=CardSpec(
                person_roles=_strings(card.get("person_roles"), "card.person_roles"),
                persons_max=persons_max,
            ),
        )


def _mapping(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShelvesConfigError(f"{where}: ожидается объект")
    return value


def _strings(value: object, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ShelvesConfigError(f"{where}: ожидается список строк")
    if len(set(value)) != len(value):
        raise ShelvesConfigError(f"{where}: значения повторяются")
    return tuple(value)


def _domains(value: object, where: str, allowed: tuple[str, ...]) -> tuple[str, ...]:
    result = _strings(value, where)
    if not result or any(d not in allowed for d in result):
        raise ShelvesConfigError(f"{where}: непустой список из {list(allowed)}")
    return result


def _system_spec(value: object, index: int, allowed: tuple[str, ...]) -> SystemShelfSpec:
    item = _mapping(value, f"system[{index}]")
    key = item.get("key")
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise ShelvesConfigError(f"system[{index}].key: ожидается {KEY_PATTERN}")
    title_key = item.get("title_key")
    if not isinstance(title_key, str) or not _I18N_KEY_RE.fullmatch(title_key):
        raise ShelvesConfigError(f"system.{key}.title_key: ключ i18n")
    return SystemShelfSpec(
        key=key,
        title_key=title_key,
        domains=_domains(item.get("domains"), f"system.{key}.domains", allowed),
        exclusive_with=_strings(item.get("exclusive_with"), f"system.{key}.exclusive_with"),
    )


@dataclass(frozen=True, slots=True)
class ShelfRef:
    """Ссылка на полку в пути: ``system:<key>`` или uuid своей полки (ровно одно из полей).

    Attributes:
        key: Ключ системной полки.
        shelf_id: Своя полка.
    """

    key: str | None = None
    shelf_id: UUID | None = None

    def __post_init__(self) -> None:
        if (self.key is None) == (self.shelf_id is None):
            raise ValueError("ShelfRef: нужен ровно один из key и shelf_id")

    @classmethod
    def parse(cls, raw: str) -> ShelfRef:
        """Строгий разбор: ``system:<key>`` или канонический uuid (нижний регистр).

        Raises:
            ShelfNotFoundError: строка не ссылка на полку (снаружи это неотличимо от
                несуществующей полки).
        """
        if raw.startswith(SYSTEM_PREFIX):
            key = raw[len(SYSTEM_PREFIX) :]
            if _KEY_RE.fullmatch(key):
                return cls(key=key)
        elif _UUID_RE.fullmatch(raw):
            return cls(shelf_id=UUID(raw))
        raise ShelfNotFoundError

    @classmethod
    def system(cls, key: str) -> ShelfRef:
        """Ссылка на системную полку."""
        return cls(key=key)

    def __str__(self) -> str:
        return f"{SYSTEM_PREFIX}{self.key}" if self.key is not None else str(self.shelf_id)
