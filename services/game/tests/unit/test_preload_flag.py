"""Имя флага предзагрузки в game совпадает с ядром: иначе валидатор и чтение расходятся."""

from __future__ import annotations

import pytest
from game_service.application import rules

from gv_game_kernel import registry

pytestmark = pytest.mark.unit


def test_preload_flag_name_matches_kernel_validator() -> None:
    assert rules.PRELOAD_FEATURE == registry.PRELOAD_FEATURE
