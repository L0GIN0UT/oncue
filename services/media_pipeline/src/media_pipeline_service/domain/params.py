"""Канонический JSON параметров и ``params_hash``.

``params_hash`` входит в уникальный ключ набора ассетов и в событие ``AssetsReady``: game
по нему понимает, что ассеты сгенерированы именно под эти параметры уровней. Поэтому хэш
не должен зависеть от порядка ключей и от записи числа (``1`` и ``1.0`` — одно значение).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any


def _normalize(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("в параметрах недопустимы NaN и бесконечность")
        return int(value) if value.is_integer() else value
    if isinstance(value, Mapping):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_normalize(v) for v in value]
    raise TypeError(f"недопустимый тип в параметрах: {type(value).__name__}")


def canonical_json(params: Mapping[str, Any]) -> str:
    """Канонический JSON: ключи отсортированы, без пробелов, целые числа без ``.0``."""
    return json.dumps(
        _normalize(params),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def params_hash(params: Mapping[str, Any]) -> str:
    """sha256 (hex) канонического JSON параметров."""
    return hashlib.sha256(canonical_json(params).encode()).hexdigest()
