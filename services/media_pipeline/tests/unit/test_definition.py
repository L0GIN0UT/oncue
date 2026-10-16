from __future__ import annotations

import pytest
from media_pipeline_service.main import DEFINITION

pytestmark = pytest.mark.unit


def test_definition_is_consistent() -> None:
    assert DEFINITION.name == "media_pipeline"
    assert DEFINITION.tables is not None
    assert DEFINITION.migrations_path is not None
    assert (DEFINITION.migrations_path / "env.py").is_file()


def test_generation_concurrency_is_limited_by_default() -> None:
    """Регресс засева 3a: 10 параллельных генераций выводили процесс за лимит памяти (OOM)."""
    from media_pipeline_service.settings import Settings

    assert Settings().task_prefetch == 2
