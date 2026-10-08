"""Проверки живости и готовности.

* ``/health/live`` — процесс жив и event loop отвечает. Никаких внешних зависимостей:
  иначе падение БД приведёт к перезапуску всех подов, что только ухудшит ситуацию.
* ``/health/ready`` — сервис может обслуживать трафик: все обязательные зависимости
  отвечают. При 503 балансировщик перестаёт слать трафик, но процесс не убивается.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    """Проверка одной зависимости. ``check`` бросает исключение, если зависимость недоступна."""

    name: str
    check: Callable[[], Awaitable[None]]
    timeout_s: float = 2.0


def postgres_check(engine: AsyncEngine, name: str = "postgres") -> ReadinessCheck:
    """Проверка PostgreSQL: ``SELECT 1`` через пул."""

    async def check() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    return ReadinessCheck(name, check)


def redis_check(client: Redis, name: str = "redis") -> ReadinessCheck:
    """Проверка Redis: ``PING``."""

    async def check() -> None:
        if not await client.ping():
            raise RuntimeError("redis ping returned false")

    return ReadinessCheck(name, check)


def health_router(checks: list[ReadinessCheck], is_shutting_down: Callable[[], bool]) -> APIRouter:
    """Роутер ``/health/*``."""
    router = APIRouter(prefix="/health", tags=["health"], include_in_schema=False)

    @router.get("/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/ready")
    async def ready() -> JSONResponse:
        if is_shutting_down():
            # Во время graceful shutdown перестаём принимать новый трафик.
            return JSONResponse({"status": "shutting_down"}, status_code=503)
        results = await asyncio.gather(*(_run(c) for c in checks))
        failed = [name for name, ok in results if not ok]
        status = 503 if failed else 200
        return JSONResponse(
            {"status": "fail" if failed else "ok", "checks": dict(results)}, status_code=status
        )

    return router


async def _run(check: ReadinessCheck) -> tuple[str, bool]:
    try:
        await asyncio.wait_for(check.check(), timeout=check.timeout_s)
    except Exception as exc:
        # Только тип ошибки: текст исключений драйверов может содержать DSN и параметры.
        log.warning("health.check_failed", check=check.name, error=type(exc).__name__)
        return check.name, False
    return check.name, True
