"""Тестовый HTTP-клиент для сервиса."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

PROBLEM_JSON = "application/problem+json"


@asynccontextmanager
async def service_client(app: FastAPI, **headers: str) -> AsyncIterator[httpx.AsyncClient]:
    """Клиент к приложению в памяти (без сети), с запуском lifespan."""
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            # Как в проде: необработанное исключение превращается в ответ 500, а не
            # пробрасывается в тест (Starlette ре-рейзит его после отправки ответа).
            transport=httpx.ASGITransport(app=manager.app, raise_app_exceptions=False),
            base_url="http://service.test",
            headers=headers,
        ) as client,
    ):
        yield client


def assert_problem(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """Проверить ответ Problem Details и вернуть тело."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON), response.headers
    body: dict[str, Any] = response.json()
    assert body["code"] == code, body
    assert body["status"] == status
    return body
