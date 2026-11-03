"""Разбор рейтинговых шаблонов очереди: шум в логах и независимость подбора (пункт 63)."""

from __future__ import annotations

from typing import Any

import pytest
from match_service.application.matchmaking import RankedTemplates
from match_service.application.ports import LadderInfo
from structlog.testing import capture_logs

from gv_game_kernel import ConfigError

pytestmark = pytest.mark.unit


class _Heads:
    def __init__(self, keys: list[tuple[str, int]]) -> None:
        self._keys = keys

    async def heads(self, domain: str) -> list[tuple[str, int]]:
        del domain
        return self._keys


class _Modes:
    async def body(self, key: str, version: int) -> dict[str, Any]:
        del key, version
        return {"stub": True}


class _BrokenFactory:
    def __init__(self) -> None:
        self.calls = 0

    def template(self, key: str, version: int, body: dict[str, Any]) -> Any:
        del key, version, body
        self.calls += 1
        raise ConfigError("не рейтинговый режим без нужных полей")


async def test_invalid_templates_log_below_warning_and_are_parsed_once() -> None:
    heads = _Heads([(f"music.other.mode{i}", 1) for i in range(9)])
    factory = _BrokenFactory()
    templates = RankedTemplates(heads, _Modes(), factory)  # type: ignore[arg-type]
    ladder = LadderInfo.__new__(LadderInfo)
    object.__setattr__(ladder, "domain", "music")
    object.__setattr__(ladder, "ladder", "music")
    with capture_logs() as logs:
        for _ in range(3):  # три входа в очередь подряд
            assert await templates.resolve(ladder, "classic") is None
    invalid = [e for e in logs if e["event"] == "match.ranked_template_invalid"]
    assert len(invalid) == 9  # по одному на режим, а не на каждый вход в очередь
    assert {e["log_level"] for e in invalid} == {"debug"}  # не warning: подбор не затронут
    assert factory.calls == 9
