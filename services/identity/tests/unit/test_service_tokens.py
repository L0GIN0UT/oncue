"""Выдача сервисных токенов (D13): use-case и конфигурация клиентов."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from identity_service.domain.service_client import ServiceClient, ServiceClientRegistry
from identity_service.settings import Settings
from identity_service.wiring import service_clients
from pydantic import ValidationError

from gv_core import ForbiddenError, InvalidInputError, RateLimitedError, UnauthenticatedError

pytestmark = pytest.mark.unit

World = Any  # фейковый мир из conftest
GAME_SECRET = "game-secret-value"
SHA = hashlib.sha256(b"x").hexdigest()


async def test_issues_token_with_configured_scopes(world: World) -> None:
    issued = await world.uc.issue_service_token.execute("game", GAME_SECRET, "media_pipeline")
    assert issued.expires_in == 600
    assert world.tokens.service_issued == [
        ("game", "media_pipeline", frozenset({"assets.link"}), 600)
    ]


@pytest.mark.parametrize(
    ("client_id", "secret"),
    [("game", "wrong"), ("game", ""), ("ghost", GAME_SECRET), ("ghost", "x")],
)
async def test_bad_credentials_are_indistinguishable(
    world: World, client_id: str, secret: str
) -> None:
    with pytest.raises(UnauthenticatedError) as exc:
        await world.uc.issue_service_token.execute(client_id, secret, "media_pipeline")
    assert exc.value.code == "auth.client_invalid"
    assert world.tokens.service_issued == []


async def test_foreign_audience_is_forbidden(world: World) -> None:
    for audience in ("identity", "guessverse"):
        with pytest.raises(ForbiddenError) as exc:
            await world.uc.issue_service_token.execute("game", GAME_SECRET, audience)
        assert exc.value.code == "auth.audience_forbidden"
    assert world.tokens.service_issued == []


async def test_failed_attempts_from_one_ip_do_not_block_other_ip(world: World) -> None:
    """Регресс DoS: неверные секреты (лимит неудач в conftest — 3) с одного IP не мешают
    честному клиенту с другого адреса."""
    uc = world.uc.issue_service_token
    for _ in range(3):
        with pytest.raises(UnauthenticatedError):
            await uc.execute("game", "wrong", "media_pipeline", source_ip="203.0.113.9")
    with pytest.raises(RateLimitedError) as exc:
        await uc.execute("game", "wrong", "media_pipeline", source_ip="203.0.113.9")
    assert exc.value.details["retry_after"] >= 1
    issued = await uc.execute("game", GAME_SECRET, "media_pipeline", source_ip="10.0.0.5")
    assert issued.expires_in == 600


async def test_successful_issues_have_separate_limit(world: World) -> None:
    uc = world.uc.issue_service_token
    for _ in range(5):  # лимит успешных выдач в conftest — 5 в минуту
        await uc.execute("game", GAME_SECRET, "media_pipeline", source_ip="10.0.0.5")
    with pytest.raises(RateLimitedError):
        await uc.execute("game", GAME_SECRET, "media_pipeline", source_ip="10.0.0.6")
    # лимит по client_id: другой клиент упирается в проверку секрета, а не в лимит
    with pytest.raises(UnauthenticatedError):
        await uc.execute("admin", "x", "identity", source_ip="10.0.0.5")


# ----------------------------------------------------------------------------- конфигурация
@pytest.mark.parametrize(
    ("client_id", "sha", "audiences"),
    [
        ("Game", SHA, {"m": ["s"]}),
        ("game", SHA.upper(), {"m": ["s"]}),
        ("game", "abc", {"m": ["s"]}),
        ("game", SHA, {}),
        ("game", SHA, {"m": []}),
        ("game", SHA, {"m": ["bad scope"]}),
        ("game", SHA, {"Bad": ["s"]}),
        ("game", SHA, {"guessverse": ["s"]}),
    ],
)
def test_invalid_client_config_is_rejected(
    client_id: str, sha: str, audiences: dict[str, list[str]]
) -> None:
    with pytest.raises(InvalidInputError) as exc:
        ServiceClient.create(client_id, sha, audiences, forbidden_audiences=("guessverse",))
    assert exc.value.code == "service_client.invalid"


def test_duplicate_client_id_is_rejected() -> None:
    client = ServiceClient.create("game", SHA, {"m": ["s"]})
    with pytest.raises(InvalidInputError):
        ServiceClientRegistry([client, client])


def test_clients_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "GV_SERVICE_CLIENTS",
        f'[{{"client_id":"game","secret_sha256":"{SHA}",'
        '"audiences":{"media_pipeline":["assets.link"]}}]',
    )
    monkeypatch.setenv("GV_SERVICE_TOKEN_LIMIT__FAILURES_PER_CLIENT_IP_PER_MIN", "7")
    settings = Settings()
    assert settings.service_token_limit.failures_per_client_ip_per_min == 7
    assert settings.service_token_limit.success_per_client_per_min == 600
    registry = service_clients(settings)
    game = registry.get("game")
    assert game is not None
    assert game.scopes_for("media_pipeline") == frozenset({"assets.link"})
    assert game.scopes_for("identity") is None


def test_public_audience_cannot_be_granted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "GV_SERVICE_CLIENTS",
        f'[{{"client_id":"game","secret_sha256":"{SHA}","audiences":{{"guessverse":["x"]}}}}]',
    )
    with pytest.raises(InvalidInputError):
        service_clients(Settings())


def test_unknown_client_fields_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "GV_SERVICE_CLIENTS",
        f'[{{"client_id":"game","secret":"plain","secret_sha256":"{SHA}","audiences":{{}}}}]',
    )
    with pytest.raises(ValidationError):
        Settings()
