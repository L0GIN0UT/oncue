from __future__ import annotations

import pytest
from bff_web_service.main import DEFINITION

pytestmark = pytest.mark.unit


def test_definition_is_consistent() -> None:
    assert DEFINITION.name == "bff_web"
