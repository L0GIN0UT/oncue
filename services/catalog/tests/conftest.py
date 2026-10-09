"""Фикстуры тестов catalog. Инфраструктура и фейки общего назначения — из gv_testkit.

Фейки портов самого catalog (репозитории в памяти и т. п.) лежат в ``tests/support``:
testkit не может их содержать — библиотекам запрещено импортировать сервисы
(import-linter, контракт ``libs-do-not-import-services``). Каталог добавляется в
``sys.path``; имя модуля уникально (``catalog_fakes``), чтобы не пересечься с другими
сервисами при общем прогоне pytest.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from catalog_service.infrastructure.container import CatalogContainer, container_for
from catalog_service.main import DEFINITION
from catalog_service.settings import AutocompleteSettings, ImportSettings, Settings

from gv_core.ids import uuid7
from gv_service import Infrastructure
from gv_testkit import InfraProvider, apply_migrations

SEEDS_DIR = Path(__file__).resolve().parents[3] / "seeds" / "catalog"
_SUPPORT = str(Path(__file__).parent / "support")
if _SUPPORT not in sys.path:
    sys.path.insert(0, _SUPPORT)


def unique_prefix() -> str:
    """Префикс ключей Redis теста (хвост uuid7 — случайные биты)."""
    return f"cattest-{uuid7().hex[-12:]}"


@pytest.fixture
async def catalog_infra(database_url: str, infra: InfraProvider) -> AsyncIterator[Infrastructure]:
    """Инфраструктура catalog на временной БД с применёнными миграциями и Redis."""
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    settings = Settings(
        database_url=database_url,
        redis_url=infra.redis_url(),
        autocomplete=AutocompleteSettings(key_prefix=unique_prefix(), rate_limit_per_minute=30),
        imports=ImportSettings(files_dir=SEEDS_DIR),
    )
    opened = await Infrastructure.open(settings, DEFINITION.tables)
    try:
        yield opened
    finally:
        await container_for(opened).close()
        await opened.close()


@pytest.fixture
def container(catalog_infra: Infrastructure) -> CatalogContainer:
    """Контейнер catalog этой инфраструктуры (адаптеры можно подменить до сборки app)."""
    return container_for(catalog_infra)
