"""Команда ``migrate``: миграции БД, затем подготовка внешних ресурсов сервиса."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from gv_service import ServiceDefinition, ServiceSettings, runner
from gv_service.runner import migrate

pytestmark = pytest.mark.unit


class _Settings(ServiceSettings):
    service_name: str = "demo"


def test_prepares_resources_after_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_run_migrations(path: Path, url: str) -> None:
        calls.append("migrations")

    monkeypatch.setattr(runner, "run_migrations", fake_run_migrations)

    async def prepare(settings: ServiceSettings) -> None:
        calls.append("resources")

    definition = ServiceDefinition(
        name="demo",
        settings_cls=_Settings,
        migrations_path=Path("migrations"),
        prepare_resources=prepare,
    )
    migrate(definition, _Settings(database_url=SecretStr("postgresql+asyncpg://u:p@h/db")))
    assert calls == ["migrations", "resources"]  # ресурсы — после схемы


def test_service_without_database_can_still_prepare_resources() -> None:
    called: list[ServiceSettings] = []

    async def prepare(settings: ServiceSettings) -> None:
        called.append(settings)

    settings = _Settings()
    migrate(
        ServiceDefinition(name="demo", settings_cls=_Settings, prepare_resources=prepare),
        settings,
    )
    assert called == [settings]


def test_migrate_without_anything_to_do_fails_loudly() -> None:
    with pytest.raises(SystemExit, match="ни миграций, ни ресурсов"):
        migrate(ServiceDefinition(name="demo", settings_cls=_Settings), _Settings())


def test_migrations_without_database_url_fail_loudly() -> None:
    definition = ServiceDefinition(
        name="demo", settings_cls=_Settings, migrations_path=Path("migrations")
    )
    with pytest.raises(SystemExit, match="GV_DATABASE_URL"):
        migrate(definition, _Settings())
