from __future__ import annotations

from typing import Any

import pytest

from gv_flags import FlagContext, StaticFlagClient, UnleashFlagClient

pytestmark = pytest.mark.unit


def test_static_defaults_and_overrides() -> None:
    flags = StaticFlagClient({"billing.enabled": False})
    assert flags.is_enabled("billing.enabled", default=True) is False
    assert flags.is_enabled("unknown", default=True) is True
    flags.set("billing.enabled", enabled=True)
    assert flags.is_enabled("billing.enabled", default=False) is True


class BrokenSdk:
    def is_enabled(self, *args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("unleash down")


class RecordingSdk:
    def __init__(self) -> None:
        self.context: dict[str, Any] = {}

    def is_enabled(self, name: str, context: dict[str, Any], fallback_function: Any) -> bool:
        self.context = context
        return name == "on"


def test_unleash_errors_fall_back_to_default() -> None:
    assert UnleashFlagClient(BrokenSdk()).is_enabled("x", default=True) is True
    assert UnleashFlagClient(BrokenSdk()).is_enabled("x", default=False) is False


def test_unleash_context_mapping() -> None:
    sdk = RecordingSdk()
    client = UnleashFlagClient(sdk)
    assert client.is_enabled(
        "on", FlagContext(user_id="u1", roles=frozenset({"b", "a"})), default=False
    )
    assert sdk.context == {"properties": {"roles": "a,b"}, "userId": "u1"}
