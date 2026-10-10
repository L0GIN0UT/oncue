"""Текст конфига режима в редакторе: разбор YAML/JSON, каноничный YAML, diff, строки по пути.

Чистые функции без Django: их проверяют unit-тесты. Конфиг хранится в game как JSON
(``jsonb``), поэтому из текста принимается только то, что представимо в JSON: даты YAML
остаются строками, повторяющийся ключ — ошибка (YAML молча взял бы последний — частая
причина «правка не применилась»).
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

Format = Literal["yaml", "json"]
FORMATS: tuple[Format, ...] = ("yaml", "json")

# Порядок ключей верхнего уровня в редакторе — как в contracts/modes/mode_config.v1.json:
# jsonb в game ключи переупорядочивает, а читать конфиг удобнее в логическом порядке.
_TOP_ORDER = (
    "key",
    "title_i18n",
    "description_i18n",
    "domain",
    "source",
    "clue",
    "answer",
    "scoring",
    "session",
    "rewards",
    "features",
)
_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"
_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


class ConfigTextError(ValueError):
    """Текст не разбирается в объект конфига.

    Attributes:
        message: Пояснение для модератора.
        line: Номер строки (с 1), если известен.
    """

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.line = line


class _JsonSafeLoader(yaml.SafeLoader):
    """SafeLoader без дат (JSON их не знает) и с запретом повторяющихся ключей."""

    # Атрибут класса, как у BaseResolver (PyYAML копирует его при add_implicit_resolver).
    yaml_implicit_resolvers = {  # noqa: RUF012
        first: [(tag, regexp) for tag, regexp in resolvers if tag != _TIMESTAMP_TAG]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def compose_node(self, parent: Node | None, index: Any) -> Node | None:  # Any: ключ или позиция
        # Якоря и ссылки (&a / *a) запрещены: при разборе PyYAML делит объекты, но json.dumps
        # и повторный дамп раскрывают их — 64 КиБ «billion laughs» превратились бы в гигабайты.
        if self.check_event(yaml.events.AliasEvent):
            event = self.peek_event()  # type: ignore[no-untyped-call]  # PyYAML без аннотаций
            raise yaml.composer.ComposerError(
                None, None, "ссылки YAML (*якорь) не поддерживаются", event.start_mark
            )
        node: Node | None = super().compose_node(parent, index)
        return node

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:  # noqa: FBT001, FBT002 — сигнатура PyYAML
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"повторяющийся ключ {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"повторяющийся ключ {key!r}")
        result[key] = value
    return result


def parse(text: str, fmt: Format) -> dict[str, Any]:
    """Текст редактора → тело конфига (объект JSON).

    Raises:
        ConfigTextError: синтаксис, повторяющийся ключ, не объект, не JSON-значения или
            слишком глубокая вложенность.
    """
    try:
        return _parse(text, fmt)
    except RecursionError as exc:
        # «[[[[…» на 64 КиБ: разбор и обход рекурсивны — ошибка формы, а не 500.
        raise ConfigTextError("слишком глубокая вложенность") from exc


def _parse(text: str, fmt: Format) -> dict[str, Any]:
    if not text.strip():
        raise ConfigTextError("пустой конфиг")
    if fmt == "json":
        try:
            data = json.loads(text, object_pairs_hook=_reject_duplicates)
        except json.JSONDecodeError as exc:
            raise ConfigTextError(f"JSON: {exc.msg}", exc.lineno) from exc
        except ValueError as exc:
            raise ConfigTextError(f"JSON: {exc}") from exc
    else:
        try:
            data = yaml.load(text, Loader=_JsonSafeLoader)  # noqa: S506 — SafeLoader-наследник
        except yaml.MarkedYAMLError as exc:
            mark = exc.problem_mark or exc.context_mark
            problem = exc.problem or exc.context or "ошибка синтаксиса"
            raise ConfigTextError(
                f"YAML: {problem}", mark.line + 1 if mark is not None else None
            ) from exc
        except yaml.YAMLError as exc:
            raise ConfigTextError(f"YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigTextError("конфиг должен быть объектом (ключ: значение)")
    try:
        # Проверка «всё представимо в JSON» (ключи-числа, бинарные значения YAML и т. п.).
        json.dumps(data, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ConfigTextError(f"значение не представимо в JSON: {exc}") from exc
    if any(not isinstance(k, str) for k in _all_keys(data)):
        raise ConfigTextError("ключи объекта должны быть строками")
    return data


def _all_keys(value: Any) -> list[Any]:
    keys: list[Any] = []
    if isinstance(value, dict):
        for k, v in value.items():
            keys.append(k)
            keys.extend(_all_keys(v))
    elif isinstance(value, list):
        for v in value:
            keys.extend(_all_keys(v))
    return keys


def _ordered(body: dict[str, Any]) -> dict[str, Any]:
    first = {k: body[k] for k in _TOP_ORDER if k in body}
    return first | {k: v for k, v in body.items() if k not in first}


def dump(body: Any, fmt: Format) -> str:
    """Тело конфига → текст редактора (логический порядок ключей верхнего уровня)."""
    data = _ordered(body) if isinstance(body, dict) else body
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)


def canonical_yaml(body: Any) -> str:
    """Каноничный YAML для сравнения: ключи отсортированы, перестановка — не изменение."""
    return yaml.safe_dump(body, allow_unicode=True, sort_keys=True, width=100)


@dataclass(frozen=True, slots=True)
class DiffLine:
    """Строка unified diff: ``kind`` — ``add``, ``del``, ``hunk``, ``ctx`` или ``meta``."""

    kind: str
    text: str


def diff(old: Any, new: Any, *, old_label: str, new_label: str) -> list[DiffLine]:
    """Unified diff каноничного YAML двух тел; пустой список — тела совпадают."""
    lines = difflib.unified_diff(
        canonical_yaml(old).splitlines(),
        canonical_yaml(new).splitlines(),
        fromfile=old_label,
        tofile=new_label,
        lineterm="",
    )
    result: list[DiffLine] = []
    for line in lines:
        if line.startswith(("---", "+++")):
            kind = "meta"
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "del"
        else:
            kind = "ctx"
        result.append(DiffLine(kind, line))
    return result


def _path_tokens(path: str) -> list[str | int] | None:
    if path in ("", "$"):
        return []
    tokens: list[str | int] = []
    pos = 0
    for match in _PATH_TOKEN.finditer(path):
        gap = path[pos : match.start()]
        if gap not in ("", "."):
            return None
        name, index = match.groups()
        tokens.append(int(index) if index is not None else name)
        pos = match.end()
    return tokens if path[pos:] == "" else None


def _child(node: Node, token: str | int) -> Node | None:
    if isinstance(node, MappingNode) and isinstance(token, str):
        for key_node, value_node in node.value:
            if isinstance(key_node, ScalarNode) and key_node.value == token:
                found: Node = value_node
                return found
    if isinstance(node, SequenceNode) and isinstance(token, int) and token < len(node.value):
        child: Node = node.value[token]
        return child
    return None


def _key_line(node: Node, token: str | int) -> int | None:
    """Строка ключа ``token`` в отображении (там модератор ищет поле глазами)."""
    if isinstance(node, MappingNode) and isinstance(token, str):
        for key_node, _ in node.value:
            if isinstance(key_node, ScalarNode) and key_node.value == token:
                line: int = key_node.start_mark.line
                return line + 1
    return None


def line_of(text: str, path: str) -> int | None:
    """Строка текста (с 1), где находится поле ``path`` (``session.rounds``, ``a.b[0]``).

    Путь ведёт к несуществующему полю (обязательное поле пропущено, путь pydantic с именем
    варианта union) — берётся самый глубокий существующий предок: ошибка всё равно видна
    рядом с нужным местом. Текст не разбирается — ``None``.
    """
    tokens = _path_tokens(path)
    if tokens is None:
        return None
    try:
        root = yaml.compose(text, Loader=_JsonSafeLoader)
    except (yaml.YAMLError, RecursionError):
        return None
    if root is None:
        return None
    node: Node = root
    line: int = root.start_mark.line + 1
    for token in tokens:
        child = _child(node, token)
        if child is None:
            break
        line = _key_line(node, token) or child.start_mark.line + 1
        node = child
    return line


def numbered(text: str, marked: set[int]) -> list[dict[str, Any]]:
    """Строки текста с номерами и признаком ошибки — для просмотра рядом с редактором."""
    return [
        {"number": i, "text": line, "error": i in marked}
        for i, line in enumerate(text.splitlines(), start=1)
    ]
