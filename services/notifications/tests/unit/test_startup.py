"""Проверки старта: секреты и адреса вне local/ci обязательны, при экспорте OpenAPI - нет."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from notifications_service.infrastructure.container import (
    NotificationsContainer,
    StartupConfigError,
)
from notifications_service.infrastructure.db.tables import TABLES
from notifications_service.infrastructure.email import (
    HttpEmailDirectory,
    UnconfiguredEmailDirectory,
)
from notifications_service.settings import NotificationsSettings, Settings
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from gv_service import Environment, Infrastructure
from gv_service.settings import ServiceAuthSettings

pytestmark = pytest.mark.unit

KEY = SecretStr("k" * 40)


def container(
    environment: Environment, *, runtime: bool = True, **notifications: Any
) -> NotificationsContainer:
    settings = Settings(
        environment=environment, notifications=NotificationsSettings(**notifications)
    )
    infra = Infrastructure(settings=settings, tables=TABLES)
    if runtime:
        infra.sessions = async_sessionmaker()  # признак настоящего процесса; без подключений
    return NotificationsContainer(infra)


FULL = {
    "unsubscribe_hmac_key": KEY,
    "public_base_url": "https://guessverse.example",
    "email_backend": "smtp",
}


def test_production_runtime_without_hmac_key_does_not_start() -> None:
    options = {k: v for k, v in FULL.items() if k != "unsubscribe_hmac_key"}
    with pytest.raises(StartupConfigError, match="HMAC"):
        container(Environment.PRODUCTION, **options)


def test_production_runtime_without_public_base_url_does_not_start() -> None:
    options = {k: v for k, v in FULL.items() if k != "public_base_url"}
    with pytest.raises(StartupConfigError, match="PUBLIC_BASE_URL"):
        container(Environment.PRODUCTION, **options)


def test_production_runtime_with_log_email_backend_does_not_start() -> None:
    options = {**FULL, "email_backend": "log"}
    with pytest.raises(StartupConfigError, match="EMAIL_BACKEND"):
        container(Environment.PRODUCTION, **options)


def test_production_runtime_with_everything_starts() -> None:
    assert container(Environment.PRODUCTION, **FULL).public_base_url == "https://guessverse.example"


@pytest.mark.parametrize("environment", [Environment.LOCAL, Environment.CI])
def test_local_and_ci_get_temporary_key_and_log_email(environment: Environment) -> None:
    c = container(environment)
    user, broadcast = uuid4(), uuid4()
    assert c.tokens.verify(c.tokens.issue(user, broadcast)) == (user, broadcast)  # ключ создан
    assert c.public_base_url.startswith("http")


def test_without_service_auth_directory_is_unconfigured() -> None:
    c = container(Environment.LOCAL)
    assert isinstance(c.directory, UnconfiguredEmailDirectory)


def test_with_service_auth_directory_is_http_identity() -> None:
    settings = Settings(
        environment=Environment.LOCAL,
        service_auth=ServiceAuthSettings(
            token_url="http://identity:8000/internal/v1/service-tokens",
            client_secret=SecretStr("s" * 40),
        ),
    )
    infra = Infrastructure(settings=settings, tables=TABLES)
    assert isinstance(NotificationsContainer(infra).directory, HttpEmailDirectory)
    assert settings.notifications.identity_url == "http://identity:8000"


def test_openapi_export_needs_no_secrets() -> None:
    """Экспорт схемы идёт в окружении по умолчанию (production) и без инфраструктуры."""
    c = container(Environment.PRODUCTION, runtime=False)
    assert c.catalog.types


def test_weak_hmac_key_is_rejected_by_settings() -> None:
    with pytest.raises(ValidationError):
        NotificationsSettings(unsubscribe_hmac_key=SecretStr("short"))


def test_previous_keys_keep_old_links_working() -> None:

    old = container(
        Environment.PRODUCTION,
        **{**FULL, "unsubscribe_hmac_key": SecretStr("o" * 40), "unsubscribe_key_version": 1},
    )
    new = container(
        Environment.PRODUCTION,
        **{
            **FULL,
            "unsubscribe_hmac_key": SecretStr("n" * 40),
            "unsubscribe_key_version": 2,
            "unsubscribe_previous_keys": {1: SecretStr("o" * 40)},
        },
    )
    user, broadcast = uuid4(), uuid4()
    assert new.tokens.verify(old.tokens.issue(user, broadcast)) == (user, broadcast)
    assert old.tokens.verify(new.tokens.issue(user, broadcast)) is None


@pytest.mark.parametrize("footer", ["Отписка: ссылка", "Отписка: {url}", "{LINK}"])
def test_footer_without_link_placeholder_is_a_startup_error(footer: str) -> None:
    """Письмо рассылки без ссылки отписки отправляться не должно никогда."""
    with pytest.raises(ValidationError, match="link"):
        NotificationsSettings(unsubscribe_footer=footer)


def test_footer_with_link_placeholder_is_accepted() -> None:
    assert NotificationsSettings(unsubscribe_footer="Отписаться: {link}")


def test_tls_modes_of_smtp_are_exclusive() -> None:
    with pytest.raises(ValidationError):
        NotificationsSettings(smtp={"use_tls": True, "start_tls": True})  # type: ignore[arg-type]


def test_empty_optional_env_values_mean_unset() -> None:
    cfg = NotificationsSettings(types_path="", public_base_url="")  # type: ignore[arg-type]
    assert cfg.types_path is None
    assert cfg.public_base_url is None
