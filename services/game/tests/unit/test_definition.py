from __future__ import annotations

import pytest
from game_service.infrastructure.container import default_token_provider
from game_service.infrastructure.media import UnconfiguredTokenProvider
from game_service.main import DEFINITION
from game_service.settings import Settings
from pydantic import SecretStr

from gv_auth import ServiceTokenProvider
from gv_service import Infrastructure, ServiceAuthSettings

pytestmark = pytest.mark.unit


def test_definition_is_consistent() -> None:
    assert DEFINITION.name == "game"
    assert DEFINITION.tables is not None
    assert DEFINITION.migrations_path is not None
    assert (DEFINITION.migrations_path / "env.py").is_file()


async def test_token_provider_from_service_auth_settings() -> None:
    settings = Settings(
        database_url=None,
        redis_url=None,
        service_auth=ServiceAuthSettings(
            token_url="http://identity.test/internal/v1/service-tokens",
            client_secret=SecretStr("s3cret"),
        ),
    )
    infra = await Infrastructure.open(settings, None)
    provider = default_token_provider(infra, "media_pipeline")
    assert isinstance(provider, ServiceTokenProvider)
    assert provider.audience == "media_pipeline"
    await infra.close()


async def test_token_provider_without_credentials_is_unconfigured() -> None:
    settings = Settings(database_url=None, redis_url=None, service_auth=ServiceAuthSettings())
    infra = await Infrastructure.open(settings, None)
    assert isinstance(default_token_provider(infra, "media_pipeline"), UnconfiguredTokenProvider)
    await infra.close()
