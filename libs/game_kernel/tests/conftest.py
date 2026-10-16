"""Общие данные тестов ядра: сиды режимов из ``seeds/game/modes`` и тестовые конфиги
режимов этапа 3a из ``tests/modes`` (сиды game для них пишет поток B)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

SEEDS_DIR = Path(__file__).resolve().parents[3] / "seeds" / "game" / "modes"
MODES_DIR = Path(__file__).resolve().parent / "modes"


@cache
def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def _find(key: str) -> Path:
    for directory in (SEEDS_DIR, MODES_DIR):
        path = directory / f"{key}.yaml"
        if path.is_file():
            return path
    raise FileNotFoundError(key)


@pytest.fixture
def seed_body() -> Callable[[str], dict[str, Any]]:
    """Фабрика: копия тела сида ``seeds/game/modes/<key>.yaml`` или тестового конфига
    ``tests/modes/<key>.yaml`` (можно менять в тесте)."""
    return lambda key: copy.deepcopy(_load(_find(key)))
