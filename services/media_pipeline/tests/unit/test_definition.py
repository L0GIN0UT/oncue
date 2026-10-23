from __future__ import annotations

import pytest
from media_pipeline_service.main import DEFINITION

pytestmark = pytest.mark.unit


def test_definition_is_consistent() -> None:
    assert DEFINITION.name == "media_pipeline"
    assert DEFINITION.tables is not None
    assert DEFINITION.migrations_path is not None
    assert (DEFINITION.migrations_path / "env.py").is_file()


def test_ugc_defaults_limit_decoding_and_cleanup() -> None:
    """DoS по декодированию и утечки объектов UGC закрыты настройками по умолчанию."""
    from media_pipeline_service.settings import Settings

    m = Settings().media
    assert m.ugc_encode_concurrency == 2
    assert m.ugc_orphan_ttl_h == 24


def test_generation_concurrency_is_limited_by_default() -> None:
    """Регресс засева 3a: 10 параллельных генераций выводили процесс за лимит памяти (OOM)."""
    from media_pipeline_service.settings import Settings

    assert Settings().task_prefetch == 2
