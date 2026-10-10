"""Встроенные периодические задачи (план этапа 2, §2.3/§2.8 п.6): очистка ключей
идемпотентности подключена к схеме сервиса без кода в самом сервисе."""

from __future__ import annotations

import pytest
from identity_service.main import DEFINITION
from identity_service.settings import Settings
from pydantic import SecretStr

from gv_service import Infrastructure
from gv_testkit import FakeClock, apply_migrations, assert_idempotency_purge

pytestmark = pytest.mark.integration


async def test_idempotency_keys_are_purged(database_url: str) -> None:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    settings = Settings(database_url=SecretStr(database_url), metrics_port=None)
    infra = await Infrastructure.open(settings, DEFINITION.tables, clock=FakeClock())
    try:
        await assert_idempotency_purge(DEFINITION, infra)
    finally:
        await infra.close()
