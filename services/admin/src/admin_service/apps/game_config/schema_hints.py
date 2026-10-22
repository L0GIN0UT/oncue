"""Подсказки редактора конфигов по JSON Schema: ветки ``session`` и заготовка нового конфига.

Источник — сводная схема game (``GET /admin/v1/mode-configs/schema``: ``{mode_config,
plugins}``, модель ядра pydantic) или сам контракт ``contracts/modes/mode_config.v1.json``:
обе формы поддерживаются. ``session`` — ``oneOf`` веток (соло | онлайн, ADR 0032) с
локальными ``$ref`` в ``$defs``; ветка выбирается по ``kind`` (``enum``/``const`` или
``discriminator.mapping``). Опциональные поля — ``anyOf [X, null]``.

Чистые функции без Django и без сети: схема приходит аргументом, неожиданная форма схемы
даёт пустые подсказки, а не ошибку страницы. Значения заготовки — из самой схемы
(``default``, ``minimum``, первое значение ``enum``): игровые величины здесь не задаются.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

SOLO = "solo"  # значение kind соло-ветки (ADR 0032: остальные — онлайн)
_MAX_DEPTH = 12  # защита от циклических $ref и патологически глубоких схем
_ATTR_LINE = re.compile(r"^\s{2,}([a-z_][a-z0-9_]*):\s*(.+)$")


@dataclass(frozen=True, slots=True)
class FieldHint:
    """Поле ветки ``session`` для таблицы подсказок.

    Attributes:
        name: Имя поля.
        required: Обязательно ли.
        type: Тип для человека: ``integer``, ``list[integer]``, ``{min, max}``, ``… | null``.
        constraints: Ограничения: ``3..300``, ``1–20 элементов``, ``= false`` (по умолчанию).
        description: Пояснение (``description`` поля или строка «Attributes» докстринга).
    """

    name: str
    required: bool
    type: str
    constraints: str
    description: str


@dataclass(frozen=True, slots=True)
class SessionVariant:
    """Ветка ``session`` (соло или онлайн).

    Attributes:
        name: Имя ветки в схеме (``OnlineSessionSpec``, ``OnlineSession``).
        kinds: Значения ``kind``, выбирающие ветку (``duel``, ``party``, ``lobby``).
        description: Первый абзац описания ветки.
        fields: Поля в порядке схемы.
        online: Онлайн-ветка (``kind`` не ``solo``) — играет match.
    """

    name: str
    kinds: tuple[str, ...]
    description: str
    fields: tuple[FieldHint, ...] = field(default=())
    online: bool = False


def mode_config_schema(schema: Any) -> dict[str, Any]:
    """Схема ``ModeConfig`` из ответа game (``{mode_config: …}``) или из контракта как есть."""
    if not isinstance(schema, dict):
        return {}
    inner = schema.get("mode_config")
    root = inner if isinstance(inner, dict) else schema
    return root if isinstance(root.get("properties"), dict) else {}


def plugin_types(schema: Any, section: str) -> list[str]:
    """Ключи плагинов секции из сводной схемы game (``plugins.contest`` → ``race``, …)."""
    plugins = schema.get("plugins") if isinstance(schema, dict) else None
    items = plugins.get(section) if isinstance(plugins, dict) else None
    return sorted(str(k) for k in items) if isinstance(items, dict) else []


def _resolve(root: dict[str, Any], node: Any, depth: int = 0) -> dict[str, Any]:
    """Раскрыть локальный ``$ref`` (``#/$defs/X``, ``#/definitions/X``); иначе — узел как есть."""
    if not isinstance(node, dict):
        return {}
    ref = node.get("$ref")
    if not isinstance(ref, str) or depth > _MAX_DEPTH:
        return node
    target: Any = root
    if not ref.startswith("#/"):
        return {}
    for part in ref[2:].split("/"):
        target = target.get(part) if isinstance(target, dict) else None
    return _resolve(root, target, depth + 1) if isinstance(target, dict) else {}


def _non_null(root: dict[str, Any], node: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """``anyOf [X, null]`` → ``(X, True)``; иначе ``(узел, False)``."""
    options = node.get("anyOf")
    if isinstance(options, list):
        real = [o for o in options if not (isinstance(o, dict) and o.get("type") == "null")]
        if len(real) == 1 and len(real) < len(options):
            return _resolve(root, real[0]), True
    return _resolve(root, node), False


def _first_paragraph(text: Any) -> str:
    return str(text).strip().split("\n\n", 1)[0].replace("\n", " ") if text else ""


def _attributes(description: Any) -> dict[str, str]:
    """Строки ``Attributes:`` докстринга модели (pydantic кладёт его в ``description``)."""
    result: dict[str, str] = {}
    if not isinstance(description, str) or "Attributes:" not in description:
        return result
    current: str | None = None
    for line in description.split("Attributes:", 1)[1].splitlines():
        match = _ATTR_LINE.match(line)
        if match and len(line) - len(line.lstrip()) <= 4:  # noqa: PLR2004 — отступ имени поля
            current = match.group(1)
            result[current] = match.group(2).strip()
        elif current and line.strip():
            result[current] += " " + line.strip()
        elif not line.strip():
            current = None
    return result


def _type_name(root: dict[str, Any], node: dict[str, Any], depth: int = 0) -> str:
    node, nullable = _non_null(root, node)
    name = "…" if depth > _MAX_DEPTH else _base_type(root, node, depth)
    return f"{name} | null" if nullable else name


def _base_type(root: dict[str, Any], node: dict[str, Any], depth: int) -> str:
    kind = node.get("type")
    props = node.get("properties")
    branches = node.get("oneOf") or node.get("anyOf")
    if isinstance(node.get("enum"), list):
        name = " | ".join(str(v) for v in node["enum"])
    elif "const" in node:
        name = str(node["const"])
    elif kind == "array":
        name = f"list[{_type_name(root, _resolve(root, node.get('items')), depth + 1)}]"
    elif isinstance(props, dict) and props:
        name = "{" + ", ".join(props) + "}"
    elif isinstance(branches, list):
        name = " | ".join(_type_name(root, _resolve(root, b), depth + 1) for b in branches)
    else:
        name = str(kind or "any")
    return name


def _constraints(root: dict[str, Any], node: dict[str, Any]) -> str:
    node, _ = _non_null(root, node)
    parts: list[str] = []
    low, high = node.get("minimum"), node.get("maximum")
    if low is not None or high is not None:
        parts.append(f"{'' if low is None else low}..{'' if high is None else high}")
    if node.get("type") == "array":
        lo, hi = node.get("minItems"), node.get("maxItems")
        if lo is not None or hi is not None:
            parts.append(f"{lo or 0}–{'∞' if hi is None else hi} элементов")
        items = _resolve(root, node.get("items"))
        if items.get("minimum") is not None or items.get("maximum") is not None:
            parts.append(f"каждый {items.get('minimum', '')}..{items.get('maximum', '')}")
    if "default" in node:
        parts.append(f"по умолчанию {json.dumps(node['default'], ensure_ascii=False)}")
    return ", ".join(parts)


def _kinds(
    root: dict[str, Any], variant: dict[str, Any], mapping: dict[str, Any], ref: Any
) -> tuple[str, ...]:
    kind = _resolve(root, (variant.get("properties") or {}).get("kind"))
    if isinstance(kind.get("enum"), list):
        return tuple(str(v) for v in kind["enum"])
    if "const" in kind:
        return (str(kind["const"]),)
    return tuple(sorted(str(k) for k, v in mapping.items() if v == ref))


def _branches(root: dict[str, Any]) -> list[tuple[dict[str, Any], tuple[str, ...]]]:
    """Ветки ``session``: ``(раскрытая схема ветки, значения kind)`` в порядке ``oneOf``."""
    session = _resolve(root, root.get("properties", {}).get("session"))
    branches = session.get("oneOf") or session.get("anyOf")
    if not isinstance(branches, list):
        return [(session, _kinds(root, session, {}, None))] if session.get("properties") else []
    discriminator = session.get("discriminator")
    mapping = discriminator.get("mapping") if isinstance(discriminator, dict) else None
    mapping = mapping if isinstance(mapping, dict) else {}
    result = []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        ref = branch.get("$ref")
        node = _resolve(root, branch)
        if not node.get("title") and isinstance(ref, str):
            # В контракте у $defs нет title: имя ветки — по $ref.
            node = {**node, "title": ref.rsplit("/", 1)[-1]}
        result.append((node, _kinds(root, node, mapping, ref)))
    return result


def session_variants(schema: Any) -> list[SessionVariant]:
    """Ветки ``session`` схемы конфига в порядке ``oneOf`` (пусто — схема не та)."""
    root = mode_config_schema(schema)
    return [_variant(root, node, kinds) for node, kinds in _branches(root)]


def _variant(root: dict[str, Any], node: dict[str, Any], kinds: tuple[str, ...]) -> SessionVariant:
    raw = node.get("properties")
    props: dict[str, Any] = raw if isinstance(raw, dict) else {}
    required = set(node.get("required") or [])
    docs = _attributes(node.get("description"))
    fields = tuple(
        FieldHint(
            name=name,
            required=name in required,
            type=_type_name(root, _resolve(root, prop)),
            constraints=_constraints(root, _resolve(root, prop)),
            description=_first_paragraph(_resolve(root, prop).get("description"))
            or docs.get(name, ""),
        )
        for name, prop in props.items()
    )
    return SessionVariant(
        name=str(node.get("title") or ""),
        kinds=kinds,
        description=_first_paragraph(node.get("description")),
        fields=fields,
        online=_is_online(kinds),
    )


def _is_online(kinds: tuple[str, ...]) -> bool:
    return bool(kinds) and SOLO not in kinds


def variant_for(variants: list[SessionVariant], kind: Any) -> SessionVariant | None:
    """Ветка по ``session.kind`` текущего текста (нет такой — ``None``)."""
    return next((v for v in variants if isinstance(kind, str) and kind in v.kinds), None)


# ---------------------------------------------------------------------------- заготовка


_LITERAL_KEYS = ("default", "const")
_SCALARS: dict[str, Any] = {"boolean": False, "string": ""}


def _skeleton(root: dict[str, Any], node: Any, depth: int = 0) -> Any:  # Any: значение JSON
    node, _ = _non_null(root, _resolve(root, node))
    literal = next((k for k in _LITERAL_KEYS if k in node), None)
    branches = node.get("oneOf") or node.get("anyOf")
    kind = node.get("type")
    if depth > _MAX_DEPTH:
        value: Any = None
    elif literal is not None:
        value = node[literal]
    elif isinstance(node.get("enum"), list) and node["enum"]:
        value = node["enum"][0]
    elif isinstance(branches, list) and branches:
        value = _skeleton(root, branches[0], depth + 1)
    elif kind == "object" or isinstance(node.get("properties"), dict):
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        required = node.get("required") or []
        value = {k: _skeleton(root, v, depth + 1) for k, v in props.items() if k in required}
    elif kind == "array":
        count = max(int(node.get("minItems") or 0), 1)
        value = [_skeleton(root, node.get("items"), depth + 1) for _ in range(count)]
    elif kind in {"integer", "number"}:
        value = node.get("minimum", 0)
    else:
        value = _SCALARS.get(str(kind), "")
    return value


def new_config(schema: Any, *, key: str, domain: str, online: bool) -> dict[str, Any] | None:
    """Заготовка нового конфига: обязательные поля по схеме, ``session`` — нужной ветки.

    Значения — из схемы (``default``, ``minimum``, первое ``enum``) и подлежат правке:
    «Проверить» покажет, что нужно заполнить. Схемы нет или ветки нет — ``None``.
    """
    root = mode_config_schema(schema)
    branch = next(((n, k) for n, k in _branches(root) if k and _is_online(k) == online), None)
    body = _skeleton(root, root) if root else None
    if branch is None or not isinstance(body, dict):
        return None
    node, kinds = branch
    session = _skeleton(root, node)
    if isinstance(session, dict):
        session["kind"] = kinds[0]
    return {**body, "key": key, "domain": domain, "session": session}
