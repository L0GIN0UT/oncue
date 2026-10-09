"""CLI admin: команды и состав компонентов процесса."""

from __future__ import annotations

import pytest
from admin_service.main import Command, Runtime, run
from admin_service.settings import Settings

pytestmark = pytest.mark.unit


def _settings(**kw: object) -> Settings:
    return Settings(metrics_port=None, **kw)  # type: ignore[arg-type]


def test_unknown_command_exits() -> None:
    with pytest.raises(SystemExit, match="неизвестная команда"):
        run(["relay"])


@pytest.mark.parametrize(
    ("command", "kafka", "expected"),
    [
        (Command.API, None, ["api"]),
        (Command.ALL, None, ["api"]),
        (Command.ALL, "redpanda:9092", ["api", "consumer"]),
        (Command.CONSUMER, "redpanda:9092", ["consumer"]),
    ],
)
def test_components(command: Command, kafka: str | None, expected: list[str]) -> None:
    runtime = Runtime(command, _settings(kafka_bootstrap=kafka))
    assert list(runtime.components()) == expected


def test_consumer_without_kafka_fails_loudly() -> None:
    with pytest.raises(SystemExit, match="GV_KAFKA_BOOTSTRAP"):
        Runtime(Command.CONSUMER, _settings()).components()


def test_every_process_gets_metrics_server() -> None:
    runtime = Runtime(Command.API, Settings(metrics_port=9100))
    assert "metrics" in runtime.components()


def test_jwks_defaults_to_identity() -> None:
    settings = Settings(jwks_url=None)
    assert settings.effective_jwks_url == "http://identity:8000/.well-known/jwks.json"
