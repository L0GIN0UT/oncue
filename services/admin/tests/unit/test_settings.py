"""Настройки admin: allowlist IP обязателен вне local/ci, ключ шифрования сессий."""

from __future__ import annotations

import pytest
from admin_service.core.session_crypto import derive_key
from admin_service.settings import DEV_IP_ALLOWLIST, AdminConfig, Settings
from cryptography.fernet import Fernet
from pydantic import ValidationError

from gv_service import Environment

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("env", [Environment.PRODUCTION, Environment.STAGING])
def test_allowlist_is_required_outside_dev(env: Environment) -> None:
    with pytest.raises(ValidationError, match="GV_ADMIN__IP_ALLOWLIST"):
        Settings(environment=env)


@pytest.mark.parametrize("env", [Environment.LOCAL, Environment.CI])
def test_dev_environments_default_to_private_networks(env: Environment) -> None:
    assert Settings(environment=env).effective_ip_allowlist == DEV_IP_ALLOWLIST


def test_explicit_allowlist_in_production() -> None:
    settings = Settings(
        environment=Environment.PRODUCTION, admin=AdminConfig(ip_allowlist=("198.51.100.0/24",))
    )
    assert settings.effective_ip_allowlist == ("198.51.100.0/24",)


@pytest.mark.parametrize("allowlist", [(), ("10.0.0.0/33",), ("офис",)])
def test_empty_or_invalid_allowlist_fails_startup(allowlist: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        AdminConfig(ip_allowlist=allowlist)


def test_invalid_session_key_fails_startup() -> None:
    with pytest.raises(ValidationError):
        AdminConfig(session_enc_key="not-a-fernet-key")
    assert AdminConfig(session_enc_key=Fernet.generate_key().decode())


def test_derived_session_key_is_valid_and_not_the_secret() -> None:
    key = derive_key("django-secret")
    Fernet(key)
    assert key == derive_key("django-secret")
    assert key != derive_key("other-secret")
    assert b"django-secret" not in key


def test_unknown_daily_timezone_fails_at_startup() -> None:
    with pytest.raises(ValidationError, match="часовой пояс"):
        AdminConfig(daily_timezone="Europe/Mosсow")  # кириллическая «с» — частая опечатка


def test_game_is_in_default_service_urls() -> None:
    assert AdminConfig().service_urls["game"].startswith("http://game")


def test_collections_is_in_default_service_urls() -> None:
    assert AdminConfig().service_urls["collections"].startswith("http://collections")


def test_rating_is_in_default_service_urls() -> None:
    assert AdminConfig().service_urls["rating"].startswith("http://rating")
