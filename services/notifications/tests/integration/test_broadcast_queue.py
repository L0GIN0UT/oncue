"""Рассылка через настоящий RabbitMQ: POST -> задача -> воркер -> письма, повтор без дублей."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
from notifications_service.application.broadcasts import BROADCAST_TASK
from notifications_service.infrastructure.db.tables import broadcast_deliveries
from notifications_service.infrastructure.messaging import build_task_handlers
from notifications_service.infrastructure.tasks import LazyRabbitTaskQueue

from gv_core.ids import uuid7
from gv_messaging.rabbit import RabbitTaskQueue
from gv_testkit import InfraProvider

pytestmark = pytest.mark.integration

Service = Any  # фикстура `notifications` описана в conftest.py


async def wait_for(condition: Any, timeout_s: float = 15.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not await condition():  # noqa: ASYNC110 — опрос с таймаутом (правила тестов)
            await asyncio.sleep(0.1)


async def test_broadcast_task_travels_through_rabbit_and_is_idempotent(
    notifications: Service, infra: InfraProvider
) -> None:
    svc = notifications
    url = infra.rabbitmq_url()
    service = f"notif_test_{uuid7().hex[-12:]}"  # свои очереди: общий брокер, чужих не трогаем
    producer = LazyRabbitTaskQueue(url, service)
    svc.tasks.forward = producer
    consumer = RabbitTaskQueue(url, service)
    await consumer.start()
    stop = asyncio.Event()
    handler = build_task_handlers(svc.infra)[BROADCAST_TASK]
    worker = asyncio.create_task(consumer.consume(BROADCAST_TASK, handler, stop))
    try:
        users = []
        for _ in range(3):
            user = uuid7()
            users.append(user)
            await svc.client.put(
                "/v1/email-subscription", json={"subscribed": True}, headers=svc.auth(user)
            )
            svc.directory.emails[user] = f"{user.hex[-8:]}@example.test"

        admin = uuid7()
        body = {"subject": "Тема", "body": "Текст", "reason": "проверка очереди"}
        headers = {**svc.admin(admin), "Idempotency-Key": "idem-queue-0001"}
        created = await svc.client.post("/admin/v1/broadcasts", json=body, headers=headers)
        assert created.status_code == 202
        broadcast_id = UUID(created.json()["id"])

        async def delivered() -> bool:
            return len(svc.email.sent) >= 3

        await wait_for(delivered)

        # повтор запроса и повторная постановка той же задачи: письма не дублируются
        await svc.client.post("/admin/v1/broadcasts", json=body, headers=headers)
        await producer.enqueue(
            BROADCAST_TASK, {"broadcast_id": str(broadcast_id)}, task_id=f"{broadcast_id}-again"
        )
        await asyncio.sleep(1.0)
        assert len(svc.email.sent) == 3
        assert (
            await svc.count(
                broadcast_deliveries, broadcast_deliveries.c.broadcast_id == broadcast_id
            )
            == 3
        )
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=10)
        await consumer.stop()
        await producer.close()
