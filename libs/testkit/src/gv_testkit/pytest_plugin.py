"""Плагин pytest, подключается автоматически при установленном gv-testkit.

Маркеры уровней (``-m unit`` и т.д.) и общие фикстуры инфраструктуры.

Импорты кода проекта — только внутри фикстур: плагин загружается раньше, чем pytest-cov
начинает измерение, и импорт на уровне модуля исказил бы покрытие библиотек.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from gv_testkit.auth import TestTokenIssuer
    from gv_testkit.fakes import FakeClock, FakeEventPublisher
    from gv_testkit.infra import InfraProvider

LEVELS = {
    "unit": "без IO, только фейки портов",
    "integration": "адаптеры на реальной инфраструктуре",
    "component": "сервис целиком через API, другие сервисы — заглушки",
    "contract": "совместимость API и событий",
    "e2e": "сценарии через весь стек",
    "slow": "медленные тесты",
}


def pytest_configure(config: pytest.Config) -> None:
    """Зарегистрировать маркеры уровней и тестовое окружение.

    По умолчанию сервис считает себя production (безопасный дефолт), поэтому тестам
    окружение задаётся явно; переменная из CI/оболочки имеет приоритет.
    """
    os.environ.setdefault("GV_ENVIRONMENT", "ci")
    for name, description in LEVELS.items():
        config.addinivalue_line("markers", f"{name}: {description}")


@pytest.fixture(scope="session")
def infra() -> Iterator[InfraProvider]:
    """Поставщик адресов реальной инфраструктуры на всю сессию."""
    from gv_testkit.infra import InfraProvider

    provider = InfraProvider()
    yield provider
    provider.close()


@pytest.fixture
async def database_url(infra: InfraProvider) -> AsyncIterator[str]:
    """Пустая временная база на тест."""
    from gv_testkit.infra import temporary_database

    async with temporary_database(infra.postgres_url()) as url:
        yield url


@pytest.fixture
def fake_clock() -> FakeClock:
    """Управляемые часы."""
    from gv_testkit.fakes import FakeClock

    return FakeClock()


@pytest.fixture
def fake_publisher() -> FakeEventPublisher:
    """Фейковый брокер событий."""
    from gv_testkit.fakes import FakeEventPublisher

    return FakeEventPublisher()


@pytest.fixture(scope="session")
def token_issuer() -> TestTokenIssuer:
    """Выпуск тестовых JWT."""
    from gv_testkit.auth import TestTokenIssuer

    return TestTokenIssuer()
