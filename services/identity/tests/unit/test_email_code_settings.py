"""Настройки кодов из писем (ADR 0041): значения по умолчанию и ограничения окружения."""

from __future__ import annotations

import pytest
from identity_service.infrastructure.security import KeyConfigError
from identity_service.settings import EmailCodeSettings, EmailSettings, Settings, SmtpSettings
from identity_service.wiring import check_email_codes_config
from pydantic import SecretStr, ValidationError

pytestmark = pytest.mark.unit

_KEY = SecretStr("k" * 32)


def test_defaults_match_adr() -> None:
    s = EmailCodeSettings()
    assert (s.length, s.ttl_s, s.max_attempts, s.resend_after_s) == (6, 600, 5, 60)
    assert s.daily_fail_limit == 20
    assert s.device_cookie_ttl_days == 180


def test_settings_build_in_default_environment() -> None:
    # Экспорт контрактов строит Settings в production по умолчанию — не должен падать.
    assert Settings().email.backend == "log"


@pytest.mark.parametrize("env", ["staging", "production"])
def test_log_backend_rejected_outside_local_ci(env: str) -> None:
    settings = Settings(environment=env, email_code=EmailCodeSettings(hmac_key=_KEY))
    with pytest.raises(KeyConfigError, match="только в local/ci"):
        check_email_codes_config(settings)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_hmac_key_required_outside_local_ci(env: str) -> None:
    settings = Settings(environment=env, email=EmailSettings(backend="smtp"))
    with pytest.raises(KeyConfigError, match="HMAC_KEY"):
        check_email_codes_config(settings)


@pytest.mark.parametrize("env", ["local", "ci"])
def test_log_backend_without_key_allowed_locally(env: str) -> None:
    check_email_codes_config(Settings(environment=env))


def test_smtp_backend_with_key_allowed_in_production() -> None:
    check_email_codes_config(
        Settings(
            environment="production",
            email=EmailSettings(backend="smtp"),
            email_code=EmailCodeSettings(hmac_key=_KEY),
        )
    )


def test_smtp_tls_modes_are_exclusive() -> None:
    with pytest.raises(ValidationError, match="взаимоисключающие"):
        SmtpSettings(use_tls=True, start_tls=True)
    assert SmtpSettings(use_tls=True, start_tls=False, port=465).use_tls
