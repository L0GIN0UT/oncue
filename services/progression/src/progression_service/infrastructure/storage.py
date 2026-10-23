"""Хранилище картинок предметов на aioboto3: бакет косметики с публичным префиксом ``c/``.

Косметика не секрет (ADR 0046): политика бакета разрешает анонимное чтение только объектов
``c/*``; gateway отдаёт их по ``/cosmetics/``. Адаптер создаётся без подключения - нет
``GV_S3__ENDPOINT`` значит 503 только у ручки загрузки, остальной сервис работает.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import aioboto3  # type: ignore[import-untyped]  # у aioboto3 нет стабов; API - как у boto3 S3
import structlog
from botocore.config import Config
from botocore.exceptions import ClientError

from gv_core.errors import DependencyUnavailableError
from progression_service.domain.shop import OBJECT_PREFIX
from progression_service.settings import S3Settings

log = structlog.get_logger(__name__)
_DELETE_BATCH = 1000  # предел DeleteObjects в S3


def cosmetics_read_policy(bucket: str) -> str:
    """Политика бакета: анонимное чтение объектов ``c/*``, без листинга и записи."""
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "PublicReadCosmeticsPrefix",
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket}/{OBJECT_PREFIX}*"],
                }
            ],
        },
        sort_keys=True,
    )


class S3MediaStore:
    """Объекты косметики: публичное чтение по префиксу, запись только по ключам сервиса."""

    def __init__(self, settings: S3Settings, *, bucket: str, base_url: str) -> None:
        self._settings = settings
        self._bucket = bucket
        self._base_url = base_url
        self._session = aioboto3.Session()
        self._config = Config(
            connect_timeout=settings.timeout_s,
            read_timeout=settings.timeout_s,
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "path"},
        )

    def _client(self) -> Any:
        s = self._settings
        if s.endpoint is None:
            raise DependencyUnavailableError(
                "хранилище не настроено (GV_S3__ENDPOINT)", code="progression.storage_unavailable"
            )
        return self._session.client(
            "s3",
            endpoint_url=s.endpoint,
            aws_access_key_id=s.access_key.get_secret_value() if s.access_key else None,
            aws_secret_access_key=s.secret_key.get_secret_value() if s.secret_key else None,
            region_name=s.region,
            config=self._config,
        )

    def public_url(self, key: str) -> str:
        """Публичный адрес объекта на gateway."""
        return f"{self._base_url}{key}"

    async def ensure_bucket(self) -> None:
        """Создать бакет и выставить политику публичного префикса (идемпотентно)."""
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
            except ClientError:
                await s3.create_bucket(Bucket=self._bucket)
            await s3.put_bucket_policy(
                Bucket=self._bucket, Policy=cosmetics_read_policy(self._bucket)
            )

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Записать объект (без ACL, с явным ``Content-Type``)."""
        async with self._client() as s3:
            await s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
            )

    async def delete(self, keys: Sequence[str]) -> None:
        """Удалить объекты пачками."""
        if not keys:
            return
        async with self._client() as s3:
            for start in range(0, len(keys), _DELETE_BATCH):
                batch = keys[start : start + _DELETE_BATCH]
                await s3.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
                )


async def prepare_storage(settings: S3Settings, *, bucket: str, base_url: str) -> None:
    """Подготовка командой ``migrate``: бакет косметики и политика публичного префикса.

    Без ``GV_S3__ENDPOINT`` (локальный запуск без хранилища) пропускается.
    """
    if settings.endpoint is None:
        log.warning("progression.storage_not_configured")
        return
    await S3MediaStore(settings, bucket=bucket, base_url=base_url).ensure_bucket()
