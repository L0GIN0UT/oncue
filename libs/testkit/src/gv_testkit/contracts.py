"""Проверка событий по контрактам из ``contracts/events`` (docs/architecture/communication.md §3).

Продюсер в тестах проверяет, что публикует ровно то, что обещано схемой; потребитель —
что умеет обрабатывать примеры из ``contracts/events/<service>/examples``. Так потоки
разработки, идущие параллельно, сходятся на одном контракте, не импортируя код друг друга.
"""

from __future__ import annotations

import json
import os
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from gv_core.ids import uuid7
from gv_messaging.envelope import EventEnvelope

ENV_CONTRACTS_DIR = "GV_CONTRACTS_DIR"
_FORMATS = FormatChecker()


def contracts_dir() -> Path:
    """Каталог ``contracts/``: из ``GV_CONTRACTS_DIR`` или ближайший вверх от рабочего."""
    override = os.environ.get(ENV_CONTRACTS_DIR)
    if override:
        return Path(override)
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "contracts" / "events").is_dir():
            return candidate / "contracts"
    raise FileNotFoundError("не найден каталог contracts/ (задайте GV_CONTRACTS_DIR)")


def _schema_path(event_type: str, version: int) -> Path:
    producer, _, event = event_type.partition(".")
    if not producer or not event:
        raise ValueError(f"event_type должен быть вида <service>.<Event>: {event_type!r}")
    return contracts_dir() / "events" / producer / f"{event}.v{version}.json"


@cache
def _validator(path: str) -> Draft202012Validator:
    schema = json.loads(Path(path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=_FORMATS)


def load_event_schema(event_type: str, version: int = 1) -> dict[str, Any]:
    """JSON Schema payload события."""
    data: dict[str, Any] = json.loads(_schema_path(event_type, version).read_text("utf-8"))
    return data


def validation_errors(event_type: str, payload: dict[str, Any], version: int = 1) -> list[str]:
    """Ошибки payload относительно схемы (пустой список — валиден)."""
    path = _schema_path(event_type, version)
    if not path.is_file():
        return [f"нет контракта {path.relative_to(contracts_dir().parent)}"]
    errors = _validator(str(path)).iter_errors(payload)
    return sorted(f"{'/'.join(map(str, e.absolute_path)) or '$'}: {e.message}" for e in errors)


def assert_event_matches_schema(envelope: EventEnvelope) -> None:
    """Конверт и payload соответствуют контрактам (envelope.v1 + схема события)."""
    envelope_errors = sorted(
        e.message
        for e in _validator(str(contracts_dir() / "events" / "envelope.v1.json")).iter_errors(
            envelope.model_dump(mode="json")
        )
    )
    errors = envelope_errors + validation_errors(
        envelope.event_type, envelope.payload, envelope.event_version
    )
    assert not errors, f"{envelope.event_type} v{envelope.event_version}: " + "; ".join(errors)


def load_example(
    event_type: str, version: int = 1, *, fresh_id: bool = True, variant: str | None = None
) -> EventEnvelope:
    """Пример события из ``contracts/events/<service>/examples`` — вход для тестов потребителя.

    Args:
        event_type: ``<service>.<Event>``.
        version: Версия схемы события.
        fresh_id: ``True`` — новый ``event_id`` (UUID v7) на каждый вызов: два загруженных
            примера — разные события, и inbox не примет второй за дубль. ``False`` —
            ``event_id`` из файла (для проверки дедупликации одного и того же события).
        variant: Вариант примера той же схемы: файл ``<Event>.<variant>.v<N>.json``
            (например ``GameFinished.collection.v1.json`` — сессия по сборнику).
    """
    producer, _, event = event_type.partition(".")
    name = f"{event}.{variant}" if variant else event
    path = contracts_dir() / "events" / producer / "examples" / f"{name}.v{version}.json"
    envelope = EventEnvelope.model_validate_json(path.read_text(encoding="utf-8"))
    if fresh_id:
        envelope = envelope.model_copy(update={"event_id": uuid7()})
    return envelope
