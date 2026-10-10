from __future__ import annotations

import pytest
from media_pipeline_service.infrastructure.container import container_for
from media_pipeline_service.infrastructure.tasks import LazyRabbitTaskQueue
from media_pipeline_service.main import DEFINITION
from media_pipeline_service.settings import Settings
from pydantic import SecretStr

from gv_core import uuid7
from gv_service import Infrastructure, create_app
from gv_testkit import InfraProvider, service_client

pytestmark = pytest.mark.component


async def test_meta_and_health() -> None:
    infra = await Infrastructure.open(Settings(), DEFINITION.tables)
    try:
        async with service_client(create_app(DEFINITION, infra)) as client:
            assert (await client.get("/v1/meta")).json() == {
                "service": "media_pipeline",
                "status": "stub",
            }
            assert (await client.get("/health/live")).status_code == 200
            assert (await client.get("/health/ready")).status_code == 200
    finally:
        await infra.close()


async def test_process_close_closes_lazy_rabbit_connection(infra: InfraProvider) -> None:
    """Ленивое соединение очереди задач закрывается в ``Infrastructure.close`` — для любой
    команды (api, consumer, worker), а не только на остановке приложения."""
    settings = Settings(rabbitmq_url=SecretStr(infra.rabbitmq_url()))
    runtime = await Infrastructure.open(settings, DEFINITION.tables)
    closed = False
    try:
        queue = container_for(runtime).adapters.queue  # как в consumer/worker: без api
        assert isinstance(queue, LazyRabbitTaskQueue)
        assert container_for(runtime).adapters.queue is queue  # один контейнер на процесс
        await queue.enqueue(f"media.shutdown_test_{uuid7().hex[-12:]}", {}, task_id="t-1")
        assert queue.is_connected
        await runtime.close()
        closed = True
        assert not queue.is_connected
    finally:
        if not closed:
            await runtime.close()
