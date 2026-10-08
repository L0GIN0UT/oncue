from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from gv_core import RequestContext, bind_context, uuid7
from gv_observability import configure_logging

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def test_json_logs_have_context_and_redact_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service="demo", env="ci", level="INFO", json=True)
    correlation = uuid7()
    with bind_context(RequestContext(correlation_id=correlation)):
        structlog.get_logger("t").info("user.login", token="s3cr3t", password="p", user_hint="ok")
    logging.getLogger("stdlib").warning("plain %s", "message")

    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    first, second = lines[0], lines[1]
    assert first["event"] == "user.login"
    assert first["service"] == "demo" and first["env"] == "ci"
    assert first["correlation_id"] == str(correlation)
    assert first["token"] == "***" and first["password"] == "***"
    assert first["user_hint"] == "ok"
    assert second["event"] == "plain message" and second["level"] == "warning"


def test_redaction_is_recursive_and_matches_variants(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service="demo", env="ci", level="INFO", json=True)
    structlog.get_logger("t").info(
        "http.request",
        new_password="p",
        api_key="k",
        client_secret="s",
        user_email="a@b.c",
        headers={"Authorization": "Bearer x", "Set-Cookie": "gv_access=y", "accept": "json"},
        items=[{"refresh_token": "r", "n": 1}],
        status=200,
    )
    rec = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    for key in ("new_password", "api_key", "client_secret", "user_email"):
        assert rec[key] == "***", key
    assert rec["headers"] == {"Authorization": "***", "Set-Cookie": "***", "accept": "json"}
    assert rec["items"] == [{"refresh_token": "***", "n": 1}]
    assert rec["status"] == 200 and rec["event"] == "http.request"


def test_stdlib_extra_fields_are_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service="demo", env="ci", level="INFO", json=True)
    logging.getLogger("lib").warning("connect", extra={"database_url": "postgres://u:p@h/db"})
    rec = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rec.get("database_url", "***") == "***"
