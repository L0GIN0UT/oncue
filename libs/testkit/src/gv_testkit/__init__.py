"""gv_testkit — общий модуль тестирования всех сервисов (см. docs/04-testing.md).

Экспорт ленивый (PEP 562): пакет импортируется pytest-плагином раньше, чем стартует
измерение покрытия, поэтому здесь нельзя тянуть код проекта на уровне модуля.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gv_testkit.api import assert_problem, service_client
    from gv_testkit.auth import TestTokenIssuer
    from gv_testkit.contracts import assert_event_matches_schema, load_example
    from gv_testkit.events import assert_published, outbox_envelopes, published_envelopes
    from gv_testkit.fakes import (
        FakeClock,
        FakeDeadLetterSink,
        FakeEventPublisher,
        FakeUnitOfWork,
        InMemoryTaskQueue,
    )
    from gv_testkit.infra import (
        InfraProvider,
        S3Endpoint,
        apply_migrations,
        create_schema,
        temporary_database,
    )
    from gv_testkit.stubs import stub_service

_EXPORTS = {
    "assert_problem": "gv_testkit.api",
    "service_client": "gv_testkit.api",
    "TestTokenIssuer": "gv_testkit.auth",
    "assert_published": "gv_testkit.events",
    "outbox_envelopes": "gv_testkit.events",
    "published_envelopes": "gv_testkit.events",
    "FakeClock": "gv_testkit.fakes",
    "FakeDeadLetterSink": "gv_testkit.fakes",
    "FakeEventPublisher": "gv_testkit.fakes",
    "FakeUnitOfWork": "gv_testkit.fakes",
    "InMemoryTaskQueue": "gv_testkit.fakes",
    "InfraProvider": "gv_testkit.infra",
    "S3Endpoint": "gv_testkit.infra",
    "assert_event_matches_schema": "gv_testkit.contracts",
    "load_example": "gv_testkit.contracts",
    "stub_service": "gv_testkit.stubs",
    "apply_migrations": "gv_testkit.infra",
    "create_schema": "gv_testkit.infra",
    "temporary_database": "gv_testkit.infra",
}

# Явный список нужен ruff/mypy; синхронность с _EXPORTS проверяет тест testkit.
__all__ = [
    "FakeClock",
    "FakeDeadLetterSink",
    "FakeEventPublisher",
    "FakeUnitOfWork",
    "InMemoryTaskQueue",
    "InfraProvider",
    "S3Endpoint",
    "TestTokenIssuer",
    "apply_migrations",
    "assert_event_matches_schema",
    "assert_problem",
    "assert_published",
    "create_schema",
    "load_example",
    "outbox_envelopes",
    "published_envelopes",
    "service_client",
    "stub_service",
    "temporary_database",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'gv_testkit' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
