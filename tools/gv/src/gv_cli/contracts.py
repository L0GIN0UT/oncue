"""Контракты: экспорт OpenAPI и схем событий, проверка актуальности и совместимости.

Источник истины — код (FastAPI-приложения сервисов и pydantic-модели), а ``contracts/`` —
зафиксированный снимок, который ревьюится в PR. CI проверяет две вещи
(см. docs/architecture/communication.md §3.4):

1. **Актуальность** — снимок совпадает с тем, что генерирует код (``gv contracts export``).
2. **Совместимость** — по сравнению с базовой веткой нет ломающих изменений.
   Ломающее изменение допустимо только как НОВАЯ версия (новый файл ``*.vN+1.json``),
   старая версия при этом должна остаться.
"""

from __future__ import annotations

import importlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gv_cli.registry import Service

CONTRACTS_DIR = Path("contracts")
OPENAPI_DIR = CONTRACTS_DIR / "openapi"
EVENTS_DIR = CONTRACTS_DIR / "events"
ENVELOPE_PATH = EVENTS_DIR / "envelope.v1.json"

_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "patch", "head", "options", "trace"})

Json = dict[str, Any]


def dump(document: Json) -> str:
    """Детерминированная сериализация: одинаковый вход — побайтно одинаковый файл."""
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# ----------------------------------------------------------------------------- export
def render_openapi(service: Service) -> Json:
    """OpenAPI сервиса без подключения к инфраструктуре.

    Приложение собирается с «пустой» ``Infrastructure`` (без engine/redis): схема зависит
    только от роутеров, поэтому экспорт работает в CI без Postgres и брокеров.
    """
    from gv_service import Infrastructure, create_app

    main = importlib.import_module(f"{service.package}.main")
    definition = main.DEFINITION
    settings = definition.settings_cls()
    app = create_app(definition, Infrastructure(settings=settings, tables=definition.tables))
    schema: Json = app.openapi()
    return schema


def render_envelope_schema() -> Json:
    """JSON Schema конверта доменного события (общий для всех событий)."""
    from gv_messaging.envelope import EventEnvelope

    schema = EventEnvelope.model_json_schema()
    schema["$id"] = "urn:guessverse:contracts:events:envelope:v1"
    return schema


def render_all(services: list[Service]) -> dict[Path, str]:
    """Все генерируемые контракты: относительный путь → содержимое."""
    files = {ENVELOPE_PATH: dump(render_envelope_schema())}
    for service in services:
        if service.is_django:
            continue  # админка (Django) не публикует API — её контракт с сервисами описан у них
        files[OPENAPI_DIR / f"{service.name}.json"] = dump(render_openapi(service))
    return files


def write_all(root: Path, services: list[Service]) -> list[Path]:
    """Записать изменившиеся файлы контрактов. Возвращает изменённые пути."""
    changed: list[Path] = []
    for rel, content in render_all(services).items():
        path = root / rel
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        changed.append(rel)
    return changed


def stale(root: Path, services: list[Service]) -> list[Path]:
    """Файлы, расходящиеся с кодом (нужно выполнить ``gv contracts export``)."""
    return [
        rel
        for rel, content in render_all(services).items()
        if not (root / rel).exists() or (root / rel).read_text(encoding="utf-8") != content
    ]


# ----------------------------------------------------------------------------- compatibility
@dataclass(frozen=True, slots=True)
class Breaking:
    """Найденное ломающее изменение контракта."""

    file: str
    detail: str

    def __str__(self) -> str:
        return f"{self.file}: {self.detail}"


def _operations(spec: Json) -> set[tuple[str, str]]:
    return {
        (path, method)
        for path, item in spec.get("paths", {}).items()
        for method in item
        if method in _HTTP_METHODS
    }


def openapi_breaking(file: str, old: Json, new: Json) -> list[Breaking]:
    """Ломающие изменения публичного API: удалённые операции.

    Более тонкий анализ (поля запросов/ответов) делает oasdiff в CI; здесь — минимальная
    защита без внешних инструментов, работающая и локально.
    """
    removed = sorted(_operations(old) - _operations(new))
    return [Breaking(file, f"удалена операция {m.upper()} {p}") for p, m in removed]


def schema_breaking(file: str, old: Json, new: Json, path: str = "$") -> list[Breaking]:
    """Ломающие изменения JSON Schema события (рекурсивно по вложенным объектам).

    Ломающими считаются: смена типа, удаление свойства, новое обязательное поле.
    Добавление необязательного поля — совместимо (потребители — tolerant readers).
    """
    problems: list[Breaking] = []
    if old.get("type") != new.get("type"):
        problems.append(Breaking(file, f"{path}: тип {old.get('type')!r} → {new.get('type')!r}"))
        return problems
    old_props: Json = old.get("properties", {})
    new_props: Json = new.get("properties", {})
    problems.extend(
        Breaking(file, f"{path}.{name}: свойство удалено")
        for name in sorted(old_props.keys() - new_props.keys())
    )
    newly_required = set(new.get("required", [])) - set(old.get("required", []))
    problems.extend(
        Breaking(file, f"{path}.{name}: поле стало обязательным") for name in sorted(newly_required)
    )
    for name in sorted(old_props.keys() & new_props.keys()):
        problems.extend(schema_breaking(file, old_props[name], new_props[name], f"{path}.{name}"))
    return problems


def _git_show(root: Path, base: str, rel: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{base}:{rel}"], cwd=root, capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def _git_ls(root: Path, base: str, rel_dir: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", base, "--", rel_dir],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.split() if result.returncode == 0 else []


def compare_with_base(root: Path, base: str) -> list[Breaking]:
    """Сравнить текущие контракты с базовой ревизией git."""
    problems: list[Breaking] = []
    for rel in _git_ls(root, base, str(CONTRACTS_DIR)):
        if not rel.endswith(".json") or "/examples/" in rel:
            continue  # примеры событий — не схемы; их валидность проверяют тесты контрактов
        old_text = _git_show(root, base, rel)
        if old_text is None:
            continue
        current = root / rel
        if not current.exists():
            problems.append(Breaking(rel, "файл контракта удалён (старая версия должна жить)"))
            continue
        old, new = json.loads(old_text), json.loads(current.read_text(encoding="utf-8"))
        if rel.startswith(str(OPENAPI_DIR)):
            problems.extend(openapi_breaking(rel, old, new))
        else:
            problems.extend(schema_breaking(rel, old, new))
    return problems
