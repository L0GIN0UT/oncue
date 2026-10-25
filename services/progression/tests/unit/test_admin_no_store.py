"""Каждая admin-ручка progression ставит ``Cache-Control: no-store`` на успешный ответ.

Заголовок выставляется после выполнения (иначе Problem ушёл бы с ним же), поэтому забытая
ручка не видна тестам конкретных операций. Проверяем исходники обработчиков по AST: в теле
каждого ``@router.<метод>`` есть заголовок ``Cache-Control`` (присваивание ``response.headers[...]``
или ``Response(headers=...)``).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from progression_service.api import admin

ADMIN_DIR = Path(admin.__file__).parent
MODULES = ("__init__.py", "events.py", "shop.py")
VERBS = {"get", "post", "put", "delete", "patch"}


def _route_handlers(tree: ast.AST) -> list[ast.AsyncFunctionDef | ast.FunctionDef]:
    handlers = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            func = deco.func if isinstance(deco, ast.Call) else deco
            if (
                isinstance(func, ast.Attribute)
                and func.attr in VERBS
                and isinstance(func.value, ast.Name)
                and func.value.id == "router"
            ):
                handlers.append(node)
    return handlers


def _sets_no_store(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.Constant) and node.value == "Cache-Control" for node in ast.walk(fn)
    )


@pytest.mark.parametrize("module", MODULES)
def test_every_admin_route_sets_no_store(module: str) -> None:
    tree = ast.parse((ADMIN_DIR / module).read_text(encoding="utf-8"))
    handlers = _route_handlers(tree)
    assert handlers, f"{module}: маршруты не найдены (проверка не работает)"
    missing = [h.name for h in handlers if not _sets_no_store(h)]
    assert not missing, f"{module}: без no-store на успешном ответе: {missing}"


def test_all_admin_modules_are_covered() -> None:
    routed = {
        p.name
        for p in ADMIN_DIR.glob("*.py")
        if _route_handlers(ast.parse(p.read_text(encoding="utf-8")))
    }
    assert routed == set(MODULES)
