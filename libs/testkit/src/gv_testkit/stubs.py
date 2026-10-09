"""Заглушки других сервисов для component-тестов (docs/04-testing.md §3).

Заглушка проверяет, что вызываемая ручка существует в контракте сервиса
(``contracts/openapi/<service>.json``): тест не сможет «договориться» с несуществующим API.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from typing import Any

import respx

from gv_testkit.contracts import contracts_dir


@cache
def _operations(service: str) -> frozenset[tuple[str, str]]:
    path = contracts_dir() / "openapi" / f"{service}.json"
    spec: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(
        (method.upper(), route) for route, item in spec.get("paths", {}).items() for method in item
    )


def _matches(template: str, path: str) -> bool:
    pattern = (
        "^"
        + re.sub(r"\{[^/]+\}", "[^/]+", re.escape(template).replace(r"\{", "{").replace(r"\}", "}"))
        + "$"
    )
    return re.match(pattern, path) is not None


def assert_in_contract(service: str, method: str, path: str) -> None:
    """Ручка ``method path`` описана в OpenAPI-снимке сервиса."""
    known = _operations(service)
    assert any(m == method.upper() and _matches(t, path) for m, t in known), (
        f"{method.upper()} {path} нет в contracts/openapi/{service}.json"
    )


class ServiceStub:
    """Заглушка одного сервиса: маршруты respx с проверкой по контракту."""

    def __init__(self, router: respx.MockRouter, base_url: str, service: str | None) -> None:
        self._router = router
        self.base_url = base_url.rstrip("/")
        self.service = service

    def route(self, method: str, path: str) -> respx.Route:
        """Маршрут заглушки; если задан ``service`` — только существующий в контракте."""
        if self.service is not None:
            assert_in_contract(self.service, method, path)
        return self._router.route(method=method.upper(), url=f"{self.base_url}{path}")


@contextmanager
def stub_service(base_url: str, service: str | None = None) -> Iterator[ServiceStub]:
    """Перехватить HTTP-вызовы к ``base_url`` (httpx) на время блока.

    Пример::

        with stub_service("http://identity:8000", service="identity") as identity:
            identity.route("POST", "/internal/v1/admin-sessions").respond(200, json={...})
    """
    with respx.mock(assert_all_called=False) as router:
        yield ServiceStub(router, base_url, service)
