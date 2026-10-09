"""Реальная инфраструктура для integration/component-тестов.

Источник адреса выбирается так:
    1. переменная окружения (``GV_TEST_POSTGRES_URL`` и т.п.) — CI с service-контейнерами
       или уже поднятый ``gv dev up infra``;
    2. testcontainers — если доступен Docker;
    3. иначе тест пропускается с понятной причиной (а не падает).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

import pytest
from sqlalchemy import MetaData, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from gv_core.ids import uuid7
from gv_db.migrations import run_migrations

ENV_POSTGRES = "GV_TEST_POSTGRES_URL"
ENV_REDIS = "GV_TEST_REDIS_URL"
ENV_KAFKA = "GV_TEST_KAFKA_BOOTSTRAP"
ENV_RABBITMQ = "GV_TEST_RABBITMQ_URL"
ENV_OPENSEARCH = "GV_TEST_OPENSEARCH_URL"
ENV_S3 = "GV_TEST_S3_ENDPOINT"

_DB_NAME_RE = re.compile(r"^[a-z0-9_]+$")
# Совпадает с init-скриптом БД (шаблон tools/gv/.../gen/service-databases.sh.j2).
SERVICE_DB_EXTENSIONS = ("pg_trgm", "citext")


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
    except Exception:
        return False
    return True


def _unavailable(reason: str) -> NoReturn:
    """Локально — пропуск теста с причиной; в CI — падение.

    В CI пропуск integration-тестов маскировал бы сломанную инфраструктуру
    и давал ложно зелёную сборку, поэтому там отсутствие зависимостей — ошибка.
    """
    if os.environ.get("CI"):
        pytest.fail(f"[CI] инфраструктура недоступна: {reason}", pytrace=False)
    pytest.skip(reason)


@dataclass(frozen=True, slots=True)
class S3Endpoint:
    """Адрес и ключи S3-совместимого хранилища для тестов."""

    url: str
    access_key: str
    secret_key: str

    @classmethod
    def parse(cls, raw: str) -> S3Endpoint:
        """Разобрать ``http://key:secret@host:port``."""
        parts = urlsplit(raw)
        if not parts.hostname or parts.username is None or parts.password is None:
            raise ValueError("ожидается http://key:secret@host:port")
        port = f":{parts.port}" if parts.port else ""
        return cls(
            url=f"{parts.scheme}://{parts.hostname}{port}",
            access_key=parts.username,
            secret_key=parts.password,
        )


class InfraProvider:
    """Ленивый поставщик адресов на всю тестовую сессию (контейнеры стартуют один раз)."""

    def __init__(self) -> None:
        self._containers: list[Any] = []
        self._cache: dict[str, str] = {}

    def _resolve(self, key: str, env: str, start: Callable[[], str]) -> str:
        if key in self._cache:
            return self._cache[key]
        value = os.environ.get(env)
        if not value:
            if not _docker_available():
                _unavailable(f"нет {env} и недоступен Docker для testcontainers")
            try:
                value = start()
            except Exception as exc:
                _unavailable(f"не удалось запустить контейнер для {key}: {exc!r}")
        self._cache[key] = value
        return value

    def postgres_url(self) -> str:
        """DSN ``postgresql+asyncpg://`` администратора тестового Postgres."""

        def start() -> str:
            from testcontainers.postgres import PostgresContainer

            c = PostgresContainer("postgres:16-alpine", driver="asyncpg").start()
            self._containers.append(c)
            return str(c.get_connection_url())

        return self._resolve("postgres", ENV_POSTGRES, start)

    def redis_url(self) -> str:
        """URL тестового Redis."""

        def start() -> str:
            from testcontainers.redis import RedisContainer

            c = RedisContainer("redis:7.4-alpine").start()
            self._containers.append(c)
            return f"redis://{c.get_container_host_ip()}:{c.get_exposed_port(6379)}/0"

        return self._resolve("redis", ENV_REDIS, start)

    def kafka_bootstrap(self) -> str:
        """Bootstrap-адрес Kafka-совместимого брокера."""

        def start() -> str:
            from testcontainers.kafka import RedpandaContainer

            c = RedpandaContainer().start()
            self._containers.append(c)
            return str(c.get_bootstrap_server())

        return self._resolve("kafka", ENV_KAFKA, start)

    def rabbitmq_url(self) -> str:
        """AMQP URL тестового RabbitMQ."""

        def start() -> str:
            from testcontainers.rabbitmq import RabbitMqContainer

            c = RabbitMqContainer("rabbitmq:4.0-alpine").start()
            self._containers.append(c)
            port = c.get_exposed_port(5672)
            return f"amqp://guest:guest@{c.get_container_host_ip()}:{port}/"

        return self._resolve("rabbitmq", ENV_RABBITMQ, start)

    def opensearch_url(self) -> str:
        """URL тестового OpenSearch (без авторизации, single-node)."""

        def start() -> str:
            from testcontainers.opensearch import OpenSearchContainer

            c = OpenSearchContainer("opensearchproject/opensearch:2.17.1").start()
            self._containers.append(c)
            port = c.get_exposed_port(9200)
            return f"http://{c.get_container_host_ip()}:{port}"

        return self._resolve("opensearch", ENV_OPENSEARCH, start)

    def s3_endpoint(self) -> S3Endpoint:
        """S3-совместимое хранилище: MinIO в контейнере или любой endpoint из env.

        Формат ``GV_TEST_S3_ENDPOINT``: ``http://key:secret@host:port`` (локально подойдёт
        ``moto_server`` из зависимостей testkit).
        """

        def start() -> str:
            from testcontainers.minio import MinioContainer

            c = MinioContainer("minio/minio:RELEASE.2024-10-13T13-34-11Z").start()
            self._containers.append(c)
            cfg = c.get_config()
            return f"http://{cfg['access_key']}:{cfg['secret_key']}@{cfg['endpoint']}"

        return S3Endpoint.parse(self._resolve("s3", ENV_S3, start))

    def close(self) -> None:
        """Остановить запущенные контейнеры."""
        for container in reversed(self._containers):
            with suppress(Exception):
                container.stop()
        self._containers.clear()


@asynccontextmanager
async def temporary_database(admin_url: str, prefix: str = "test") -> AsyncIterator[str]:
    """Создать пустую базу с уникальным именем и удалить её после блока.

    Каждый тестовый модуль получает свою базу — тесты изолированы и могут идти параллельно.
    """
    # Хвост uuid7 — случайные биты; начало — миллисекунды времени и совпадает у тестов,
    # стартовавших одновременно (параллельные прогоны в нескольких рабочих копиях).
    name = f"{prefix}_{uuid7().hex[-12:]}"
    if not _DB_NAME_RE.match(name):
        raise ValueError(f"недопустимое имя базы: {name}")
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
        url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        # Те же расширения, что создаёт init-скрипт БД на проде (сервисной роли нельзя).
        ext_engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
        try:
            async with ext_engine.connect() as conn:
                for extension in SERVICE_DB_EXTENSIONS:
                    await conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))
        finally:
            await ext_engine.dispose()
        yield url
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


async def create_schema(url: str, metadata: MetaData) -> None:
    """Создать таблицы напрямую из метаданных (быстро, для тестов библиотек)."""
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    await engine.dispose()


async def apply_migrations(url: str, migrations_path: Path) -> None:
    """Применить настоящие миграции сервиса (проверяем и сами миграции)."""
    await asyncio.to_thread(run_migrations, migrations_path, url)
