"""Граф зависимостей workspace и определение затронутых изменениями модулей."""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

_REQ_NAME = re.compile(r"^[A-Za-z0-9_.\-]+")


@dataclass(frozen=True, slots=True)
class Member:
    """Участник uv workspace."""

    name: str  # имя дистрибутива
    path: Path  # каталог относительно корня
    deps: frozenset[str]  # зависимости-участники workspace


def load_members(root: Path) -> dict[str, Member]:
    """Прочитать всех участников workspace (libs/*, services/*, tools/gv)."""
    paths = [
        *sorted((root / "libs").glob("*/pyproject.toml")),
        *sorted((root / "services").glob("*/pyproject.toml")),
        root / "tools" / "gv" / "pyproject.toml",
    ]
    raw: dict[str, tuple[Path, list[str]]] = {}
    for pyproject in paths:
        if not pyproject.exists():
            continue
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        deps = []
        for req in project.get("dependencies", []):
            match = _REQ_NAME.match(req)
            if match:
                deps.append(match.group(0).lower().replace("_", "-"))
        raw[project["name"]] = (pyproject.parent.relative_to(root), deps)
    names = set(raw)
    return {
        name: Member(name, path, frozenset(d for d in deps if d in names))
        for name, (path, deps) in raw.items()
    }


def dependents(members: dict[str, Member], changed: set[str]) -> set[str]:
    """Изменённые участники + все, кто от них зависит (транзитивно)."""
    result = set(changed)
    grew = True
    while grew:
        grew = False
        for member in members.values():
            if member.name not in result and member.deps & result:
                result.add(member.name)
                grew = True
    return result


def changed_members(root: Path, base: str) -> set[str]:
    """Участники, затронутые diff относительно ``base`` (+ их зависимые).

    Изменение корневых файлов (lock, общие настройки) затрагивает всех.
    """
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    out += subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    members = load_members(root)
    global_files = {"pyproject.toml", "uv.lock", ".importlinter"}
    if any(f in global_files for f in out):
        return set(members)
    touched = {m.name for m in members.values() for f in out if Path(f).is_relative_to(m.path)}
    return dependents(members, touched)
