from __future__ import annotations

import pytest
from collections_service.main import DEFINITION

pytestmark = pytest.mark.unit


def test_definition_is_consistent() -> None:
    assert DEFINITION.name == "collections"
    assert DEFINITION.tables is not None
    assert DEFINITION.migrations_path is not None
    assert (DEFINITION.migrations_path / "env.py").is_file()
