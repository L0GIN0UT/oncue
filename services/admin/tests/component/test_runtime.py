"""Процесс ``admin api``: Django под uvicorn, метрики на отдельном порту, graceful stop."""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
from admin_service.main import Command, Runtime
from admin_service.settings import Settings

pytestmark = pytest.mark.component


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_ok(client: httpx.AsyncClient, url: str) -> httpx.Response:
    for _ in range(200):
        try:
            response = await client.get(url)
        except httpx.TransportError:
            await asyncio.sleep(0.05)
            continue
        if response.status_code == 200:
            return response
        await asyncio.sleep(0.05)
    pytest.fail(f"{url} не ответил 200")


async def test_api_serves_django_and_metrics_then_stops() -> None:
    port, metrics_port = _free_port(), _free_port()
    runtime = Runtime(
        Command.API, Settings(http_host="127.0.0.1", http_port=port, metrics_port=metrics_port)
    )
    task = asyncio.create_task(runtime.run())
    async with httpx.AsyncClient() as client:
        live = await _wait_ok(client, f"http://127.0.0.1:{port}/health/live")
        assert live.json() == {"status": "ok"}
        # На основном порту метрик нет (там админка: редирект на вход).
        main_metrics = await client.get(f"http://127.0.0.1:{port}/metrics")
        assert main_metrics.status_code != 200
        assert "gv_http_requests_total" not in main_metrics.text
        await _wait_ok(client, f"http://127.0.0.1:{metrics_port}/metrics")
    runtime.request_shutdown()
    await asyncio.wait_for(task, timeout=10)
