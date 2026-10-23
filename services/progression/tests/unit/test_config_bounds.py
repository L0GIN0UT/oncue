"""Верхние границы конфига: домен повторяет ``maximum``/``maxItems`` схемы (как ladder в rating)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from progression_service.domain.config import (
    BOUNDS,
    ITEM_LIMITS,
    MAX_BOOST_MULTIPLIER,
    ConfigInvalidError,
    ProgressionConfig,
)

pytestmark = pytest.mark.unit

SCHEMA = (
    Path(__file__).resolve().parents[4] / "contracts" / "progression" / "progression_config.v1.json"
)

# Поля, которые разбирает другой поток (P3 — admin): домену границы не нужны, все остальные
# границы схемы (в том числе задания, бейджи и колесо - P2) обязаны быть в BOUNDS.
# Секции 5b (`shop`, `promo`, `spend`) пока только в схеме (шаг 0a): домен разберёт их поток S1,
# он же вынесет границы в BOUNDS и уберёт эти префиксы отсюда.
OTHER_STREAMS = ("admin.", "shop.", "promo.", "spend.")


def _walk(node: Any, path: str, out: dict[str, Any]) -> None:
    """Собрать ``{путь: узел}`` для узлов схемы с ``maximum``/``maxItems``."""
    if not isinstance(node, dict):
        return
    if "maximum" in node or "maxItems" in node:
        out[path] = node
    for name, child in node.get("properties", {}).items():
        _walk(child, f"{path}.{name}" if path else name, out)
    if "items" in node:
        _walk(node["items"], f"{path}[]", out)


def _schema_nodes() -> dict[str, Any]:
    out: dict[str, Any] = {}
    _walk(json.loads(SCHEMA.read_text(encoding="utf-8")), "", out)
    return out


def test_domain_bounds_match_schema_maximums() -> None:
    nodes = _schema_nodes()
    in_scope = {p: n for p, n in nodes.items() if not p.startswith(OTHER_STREAMS)}
    maximums = {p: n["maximum"] for p, n in in_scope.items() if "maximum" in n}
    limits = {p: n["maxItems"] for p, n in in_scope.items() if "maxItems" in n}

    assert dict(BOUNDS) == maximums
    assert dict(ITEM_LIMITS) == limits
    assert MAX_BOOST_MULTIPLIER == 10


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    """Мутация: записать ``value`` в поле по пути ``a.b[0].c`` (первый элемент массива)."""

    def apply(body: dict[str, Any]) -> None:
        *head, last = path.replace("[]", ".0").split(".")
        target: Any = body
        for part in head:
            target = target[int(part)] if part.isdigit() else target[part]
        if last.isdigit():
            target[int(last)] = value
        else:
            target[last] = value

    return apply


def _grow(path: str) -> Callable[[dict[str, Any]], None]:
    """Мутация: удлинить массив ``path`` выше ``ITEM_LIMITS`` копиями последнего элемента."""

    def apply(body: dict[str, Any]) -> None:
        target: Any = body
        for part in path.split("."):
            target = target[part]
        target.extend([target[-1]] * ITEM_LIMITS[path])

    return apply


@pytest.mark.parametrize("path", sorted(p for p in BOUNDS if p != "min_rounds_ratio"))
def test_value_above_bound_is_rejected(
    make_config: Callable[..., dict[str, Any]], path: str
) -> None:
    body = make_config()
    _set(path, BOUNDS[path] + 1)(body)
    with pytest.raises(ConfigInvalidError):
        ProgressionConfig.parse(body)


def test_value_at_bound_is_accepted(make_config: Callable[..., dict[str, Any]]) -> None:
    body = make_config()
    _set("levels.max_level", int(BOUNDS["levels.max_level"]))(body)
    boost = next(
        s["prize"] for s in body["wheel"]["sectors"] if s["prize"].get("multiplier") is not None
    )
    boost["multiplier"] = MAX_BOOST_MULTIPLIER.__float__()
    ProgressionConfig.parse(body)


@pytest.mark.parametrize("path", sorted(ITEM_LIMITS))
def test_array_longer_than_limit_is_rejected(
    make_config: Callable[..., dict[str, Any]], path: str
) -> None:
    body = make_config()
    _grow(path)(body)
    with pytest.raises(ConfigInvalidError):
        ProgressionConfig.parse(body)


def test_boost_multiplier_must_be_above_one(make_config: Callable[..., dict[str, Any]]) -> None:
    body = make_config()
    boosts = [
        s["prize"] for s in body["wheel"]["sectors"] if s["prize"].get("multiplier") is not None
    ]
    assert boosts
    boosts[0]["multiplier"] = 1
    with pytest.raises(ConfigInvalidError):
        ProgressionConfig.parse(body)
