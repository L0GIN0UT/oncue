from __future__ import annotations

import pytest
from notifications_service.main import DEFINITION

pytestmark = pytest.mark.unit


def test_definition_is_consistent() -> None:
    assert DEFINITION.name == "notifications"
    assert DEFINITION.tables is not None
    assert DEFINITION.migrations_path is not None
    assert (DEFINITION.migrations_path / "env.py").is_file()
