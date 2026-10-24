"""Сервис объявляет в pyproject все внутренние библиотеки `gv_*`, которые импортирует.

Образ собирается `uv sync --package guessverse-<сервис>` и ставит только объявленное
(прямо или через другие библиотеки). Незаявленный импорт локально незаметен (ставятся
все пакеты), а в контейнере роняет процесс на старте (так упал `social migrate` в CI).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from gv_cli.registry import repo_root

pytestmark = pytest.mark.unit

ROOT = repo_root(Path(__file__))
IMPORT = re.compile(r"^\s*(?:from|import)\s+(gv_[a-z0-9_]+)", re.MULTILINE)
TESTKIT = "gv-testkit"


def _deps(pyproject: Path) -> set[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return {re.split(r"[\[<>=~! ]", d, maxsplit=1)[0] for d in data["project"]["dependencies"]}


def _libs() -> dict[str, set[str]]:
    return {
        f"gv-{p.parent.name.replace('_', '-')}": _deps(p)
        for p in (ROOT / "libs").glob("*/pyproject.toml")
    }


def _closure(direct: set[str], libs: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    todo = list(direct)
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(libs.get(name, ()))
    return seen


def _service_dirs() -> list[Path]:
    return sorted(p.parent for p in (ROOT / "services").glob("*/pyproject.toml"))


@pytest.mark.parametrize("service", _service_dirs(), ids=lambda p: p.name)
def test_imported_gv_libs_are_declared(service: Path) -> None:
    libs = _libs()
    available = _closure(_deps(service / "pyproject.toml"), libs)
    imported = {
        m.replace("_", "-")
        for path in (service / "src").rglob("*.py")
        for m in IMPORT.findall(path.read_text(encoding="utf-8"))
    }
    missing = {name for name in imported if name in libs and name not in available}
    assert not missing, f"{service.name}: нет в dependencies pyproject: {sorted(missing)}"
