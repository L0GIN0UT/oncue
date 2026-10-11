"""Общие данные тестов ядра: сиды режимов из ``seeds/game/modes``."""

from __future__ import annotations

import copy
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

SEEDS_DIR = Path(__file__).resolve().parents[3] / "seeds" / "game" / "modes"


@cache
def _load(key: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((SEEDS_DIR / f"{key}.yaml").read_text(encoding="utf-8"))
    return data


@pytest.fixture
def seed_body() -> Callable[[str], dict[str, Any]]:
    """Фабрика: копия тела сида ``seeds/game/modes/<key>.yaml`` (можно менять в тесте)."""
    return lambda key: copy.deepcopy(_load(key))
