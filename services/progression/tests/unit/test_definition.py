from __future__ import annotations

import pytest
from progression_service.main import DEFINITION

pytestmark = pytest.mark.unit


def test_definition_is_consistent() -> None:
    assert DEFINITION.name == "progression"
    assert DEFINITION.tables is not None
    assert DEFINITION.migrations_path is not None
    assert (DEFINITION.migrations_path / "env.py").is_file()


def test_consumes_ugc_moderation_topic_of_the_contract() -> None:
    from gv_testkit.contracts import load_event_schema

    topic = load_event_schema("media_pipeline.AssetModerated")["x-topic"]
    assert topic in DEFINITION.consumer_topics
