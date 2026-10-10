"""Сборка клиента сервисных токенов из ``GV_SERVICE_AUTH__*`` (D13)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from gv_service import (
    Infrastructure,
    ServiceAuthNotConfiguredError,
    ServiceSettings,
    build_service_token_provider,
)

pytestmark = pytest.mark.unit


async def test_provider_from_settings_is_closed_with_process() -> None:
    settings = ServiceSettings(
        service_name="game",
        metrics_port=None,
        service_auth={"token_url": "http://identity/t", "client_secret": SecretStr("s3cr3t")},
    )
    infra = Infrastructure(settings=settings, tables=None)
    provider = build_service_token_provider(infra, "media_pipeline")
    assert provider.audience == "media_pipeline"
    assert "client_id='game'" in repr(provider)  # client_id по умолчанию — имя сервиса
    assert "s3cr3t" not in repr(provider)
    await infra.close()  # closer зарегистрирован и отрабатывает без открытого клиента


async def test_missing_credentials_fail_fast() -> None:
    infra = Infrastructure(settings=ServiceSettings(service_name="game"), tables=None)
    with pytest.raises(ServiceAuthNotConfiguredError):
        build_service_token_provider(infra, "media_pipeline")


def test_internal_audience_defaults_to_service_name_and_differs_from_public() -> None:
    assert ServiceSettings(service_name="media_pipeline").internal_audience == "media_pipeline"
    with pytest.raises(ValidationError):
        ServiceSettings(service_name="x", internal_auth={"audience": "guessverse"})
