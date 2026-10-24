"""Каталог типов уведомлений: разбор конфига и проверка согласованности (ADR 0047).

Типов и текстов в коде нет: всё приходит из ``config/notification_types.yaml``. Каталог
отказывается собираться, если у типа нет шаблона на ``default_locale`` или шаблон ссылается
на параметр, которого нет, — сервис в этом случае не стартует.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from notifications_service.domain.errors import ConfigError

PAYLOAD_ROOT = "payload"
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
DEDUP_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")

type FilterValue = str | int | float | bool


@dataclass(frozen=True, slots=True)
class Channels:
    """Каналы типа по умолчанию."""

    in_app: bool
    email: bool
    push: bool


@dataclass(frozen=True, slots=True)
class Template:
    """Заголовок и текст на одной локали; плейсхолдеры ``{имя}`` из ``params``."""

    title: str
    body: str


@dataclass(frozen=True, slots=True)
class Lookup:
    """Справочник текстов по коду из события (например, причина отклонения).

    Attributes:
        path: Путь к коду в событии (``payload.reason_code``).
        texts: Код -> локаль -> текст фрагмента.
        fallback: Локаль -> текст для неизвестного или пустого кода.
    """

    path: str
    texts: Mapping[str, Mapping[str, str]]
    fallback: Mapping[str, str]

    def resolve(self, code: str, locale: str, default_locale: str) -> str:
        """Текст по коду; нет кода - fallback; нет локали - ``default_locale``."""
        by_locale = self.texts.get(code, self.fallback)
        return by_locale.get(locale) or by_locale[default_locale]


@dataclass(frozen=True, slots=True)
class NotificationType:
    """Тип уведомления: что слушаем, кому, что показываем.

    Attributes:
        key: Ключ типа (стабильный контракт настроек игрока).
        source_event: ``<producer>.<Event>``.
        recipient: Путь к ``user_id`` получателя (``payload.x``).
        channels: Каналы по умолчанию.
        user_mutable: Игрок может отключить тип.
        dedup_key: Шаблон ключа дедупликации из полей payload (не ``event_id``).
        params: Подстановки шаблона: имя -> путь в событии.
        templates: Шаблоны по локали.
        filter: Условие равенства по полям payload.
    """

    key: str
    source_event: str
    recipient: str
    channels: Channels
    user_mutable: bool
    dedup_key: str
    params: Mapping[str, str]
    templates: Mapping[str, Template]
    filter: Mapping[str, FilterValue] = field(default_factory=dict)
    lookups: Mapping[str, Lookup] = field(default_factory=dict)


def _require_payload_path(type_key: str, field_name: str, path: str) -> None:
    if not path.startswith(f"{PAYLOAD_ROOT}.") or path == f"{PAYLOAD_ROOT}.":
        raise ConfigError(f"{type_key}: {field_name} должен начинаться с `{PAYLOAD_ROOT}.`: {path}")


def _parse_lookups(
    key: str, raw: Mapping[str, Any], params: Mapping[str, str], default_locale: str
) -> dict[str, Lookup]:
    lookups: dict[str, Lookup] = {}
    for name, item in raw.items():
        if name in params:
            raise ConfigError(f"{key}: `{name}` одновременно в params и lookups")
        _require_payload_path(key, f"lookups.{name}.path", item["path"])
        fallback = dict(item["fallback"])
        if default_locale not in fallback:
            raise ConfigError(f"{key}: lookups.{name}.fallback без `{default_locale}`")
        texts = {code: dict(by_locale) for code, by_locale in item["texts"].items()}
        for code, by_locale in texts.items():
            if default_locale not in by_locale:
                raise ConfigError(f"{key}: lookups.{name}.texts.{code} без `{default_locale}`")
        lookups[name] = Lookup(path=item["path"], texts=texts, fallback=fallback)
    return lookups


def _parse_type(raw: Mapping[str, Any], default_locale: str) -> NotificationType:
    key = str(raw["key"])
    _require_payload_path(key, "recipient", raw["recipient"])
    params: dict[str, str] = dict(raw["params"])
    for name, path in params.items():
        _require_payload_path(key, f"params.{name}", path)
    lookups = _parse_lookups(key, raw.get("lookups", {}), params, default_locale)
    refs = DEDUP_PLACEHOLDER.findall(raw["dedup_key"])
    if not refs:
        raise ConfigError(f"{key}: dedup_key без подстановок из полей события")
    for ref in refs:
        if ref == "event_id" or not ref.startswith(f"{PAYLOAD_ROOT}."):
            raise ConfigError(f"{key}: dedup_key берётся из полей payload, не из `{ref}`")
    templates = {
        locale: Template(title=t["title"], body=t["body"]) for locale, t in raw["templates"].items()
    }
    if default_locale not in templates:
        raise ConfigError(f"{key}: нет шаблона на default_locale `{default_locale}`")
    for locale, template in templates.items():
        for text in (template.title, template.body):
            unknown = set(PLACEHOLDER.findall(text)) - set(params) - set(lookups)
            if unknown:
                raise ConfigError(
                    f"{key}: шаблон `{locale}` ссылается на неизвестные параметры {sorted(unknown)}"
                )
    channels = raw["channels"]
    return NotificationType(
        key=key,
        source_event=raw["source_event"],
        recipient=raw["recipient"],
        channels=Channels(channels["in_app"], channels["email"], channels["push"]),
        user_mutable=bool(raw["user_mutable"]),
        dedup_key=raw["dedup_key"],
        params=params,
        templates=templates,
        filter=dict(raw.get("filter", {})),
        lookups=lookups,
    )


@dataclass(frozen=True, slots=True)
class TypeCatalog:
    """Все типы уведомлений сервиса."""

    default_locale: str
    types: tuple[NotificationType, ...]

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> TypeCatalog:
        """Собрать каталог из документа конфига (уже прошедшего JSON Schema).

        Raises:
            ConfigError: ключи не уникальны, нет шаблона на ``default_locale``, плейсхолдер без
                параметра, ``dedup_key`` не из полей payload.
        """
        default_locale = str(raw["default_locale"])
        types = tuple(_parse_type(item, default_locale) for item in raw["types"])
        keys = [t.key for t in types]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        if duplicates:
            raise ConfigError(f"ключи типов не уникальны: {duplicates}")
        return cls(default_locale=default_locale, types=types)

    def by_key(self, key: str) -> NotificationType | None:
        """Тип по ключу или ``None``."""
        return next((t for t in self.types if t.key == key), None)

    def for_event(self, event_type: str) -> tuple[NotificationType, ...]:
        """Типы, которые слушают событие."""
        return tuple(t for t in self.types if t.source_event == event_type)

    @property
    def source_events(self) -> frozenset[str]:
        """Все события-источники."""
        return frozenset(t.source_event for t in self.types)
