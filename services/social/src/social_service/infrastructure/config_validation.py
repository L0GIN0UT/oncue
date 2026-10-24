"""Проверка тела конфига social: JSON Schema контракта + смысловые правила (ADR 0053)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator

from social_service.domain.config_rules import ConfigInvalidError, semantic_errors

SCHEMA_FILE = "social_config.schema.json"


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    text = resources.files("social_service.config").joinpath(SCHEMA_FILE).read_text("utf-8")
    return Draft202012Validator(json.loads(text))


def validate_config_body(body: Mapping[str, Any]) -> None:
    """Проверить тело версии.

    Raises:
        ConfigInvalidError: нарушение схемы или правил между полями (``social.config_invalid``).
    """
    errors = [
        {"path": "/".join(map(str, e.absolute_path)) or "$", "reason": e.message}
        for e in sorted(_validator().iter_errors(dict(body)), key=lambda e: list(e.absolute_path))
    ]
    if errors:
        raise ConfigInvalidError(errors[:20])
    semantic = semantic_errors(body)
    if semantic:
        raise ConfigInvalidError(semantic)
