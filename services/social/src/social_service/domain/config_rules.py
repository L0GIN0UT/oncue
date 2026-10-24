"""Смысловые правила конфига social, которые не выразить схемой (ADR 0053)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gv_core.errors import InvalidInputError

CONFIG_INVALID = "social.config_invalid"


class ConfigInvalidError(InvalidInputError):
    """Тело конфига не проходит проверку; ``details.errors`` - пути и причины."""

    default_code = CONFIG_INVALID

    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__(
            f"конфиг social невалиден: {len(errors)} ошибок",
            code=CONFIG_INVALID,
            details={"errors": errors},
        )


def semantic_errors(body: Mapping[str, Any]) -> list[dict[str, str]]:
    """Ошибки правил между полями (тело уже прошло схему)."""
    errors: list[dict[str, str]] = []
    presence = body["presence"]
    if presence["ttl_s"] <= presence["heartbeat_s"]:
        errors.append({"path": "presence.ttl_s", "reason": "ttl_s должен быть больше heartbeat_s"})
    sanctions = body["sanctions"]
    if sanctions["min_duration_s"] > sanctions["max_duration_s"]:
        errors.append(
            {"path": "sanctions.min_duration_s", "reason": "min_duration_s больше max_duration_s"}
        )
    codes = [r["code"] for r in body["reports"]["reasons"]]
    if len(set(codes)) != len(codes):
        errors.append({"path": "reports.reasons", "reason": "коды причин не уникальны"})
    return errors
