"""Загрузка типов уведомлений из YAML и проверка по JSON Schema контракта.

Файл по умолчанию лежит внутри пакета (``config/notification_types.yaml``): образ ставится без
editable-режима. ``GV_NOTIFICATIONS__TYPES_PATH`` подменяет его целиком. Схема в
``config/notification_types.schema.json`` - копия контракта
``contracts/notifications/notification_types.v1.json`` (совпадение проверяет contract-тест).
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from notifications_service.domain.catalog import TypeCatalog
from notifications_service.domain.errors import ConfigError

BUILTIN_TYPES = "notification_types.yaml"
SCHEMA_FILE = "notification_types.schema.json"


def _builtin(name: str) -> str:
    return resources.files("notifications_service.config").joinpath(name).read_text("utf-8")


def read_document(path: Path | None = None) -> dict[str, Any]:
    """Прочитать YAML типов (``safe_load``: без исполнения тегов).

    Raises:
        ConfigError: файл нечитаем, не YAML или верхний уровень не объект.
    """
    try:
        text = _builtin(BUILTIN_TYPES) if path is None else path.read_text(encoding="utf-8")
        document = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"не удалось прочитать конфиг типов: {exc}") from exc
    if not isinstance(document, dict):
        raise ConfigError("notification_types: ожидается объект верхнего уровня")
    return document


def validate_document(document: dict[str, Any]) -> None:
    """Проверить документ по схеме контракта.

    Raises:
        ConfigError: со списком путей и причин.
    """
    schema = json.loads(_builtin(SCHEMA_FILE))
    errors = sorted(
        f"{'/'.join(map(str, e.absolute_path)) or '$'}: {e.message}"
        for e in Draft202012Validator(schema).iter_errors(document)
    )
    if errors:
        raise ConfigError("конфиг типов не соответствует схеме: " + "; ".join(errors[:10]))


def load_catalog(path: Path | None = None) -> TypeCatalog:
    """Каталог типов; любая ошибка конфига - сразу при старте процесса."""
    document = read_document(path)
    validate_document(document)
    return TypeCatalog.parse(document)
