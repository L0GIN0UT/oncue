"""S3-совместимое хранилище на aioboto3: приватные бакеты исходников и ассетов.

Код работает через стандартный S3 API: на VPS — SeaweedFS (ADR 0021), в тестах —
``moto_server``, в облаке — любой S3-совместимый сервис.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import aioboto3  # type: ignore[import-untyped]  # у aioboto3 нет стабов; API — как у boto3 S3
from botocore.config import Config
from botocore.exceptions import ClientError

from gv_core.errors import DependencyUnavailableError
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import ObjectPage, StorageArea, StoredObject
from media_pipeline_service.domain.links import ASSET_PREFIX
from media_pipeline_service.domain.ugc import PENDING_PREFIX, PUBLIC_PREFIX
from media_pipeline_service.settings import S3Settings

_DELETE_BATCH = 1000  # предел DeleteObjects в S3


def assets_read_policy(bucket: str) -> str:
    """Политика бакета ассетов: анонимное чтение ``a/*``, ``p/*``, ``u/*`` (ADR 0012, 0048).

    Хранилище доступно лишь во внутренней сети: читает его gateway, который сам проверяет
    подпись ссылки (``secure_link``) и не передаёт хранилищу query и заголовки клиента.
    Снаружи ``a/`` и ``p/`` (UGC на модерации) доступны только по подписанной ссылке, ``u/``
    (одобренные UGC) - публично через ``/ugc/``. Запись, удаление, листинг и любые объекты
    вне префиксов остаются только по ключам.
    """
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "GatewayReadsAssetsPrefix",
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": ["s3:GetObject"],
                    "Resource": [
                        f"arn:aws:s3:::{bucket}/{prefix}*"
                        for prefix in (ASSET_PREFIX, PENDING_PREFIX, PUBLIC_PREFIX)
                    ],
                }
            ],
        },
        sort_keys=True,
    )


class S3Storage:
    """Объекты без ACL (приватные), с явным ``Content-Type`` и без пользовательских метаданных.

    Gateway раздаёт бакет ассетов по префиксу ``a/`` через ``secure_link`` (ADR 0012);
    бакет исходников наружу не публикуется.
    """

    def __init__(self, settings: S3Settings) -> None:
        self._settings = settings
        self._session = aioboto3.Session()
        self._buckets = {
            StorageArea.SOURCES: settings.bucket_sources,
            StorageArea.ASSETS: settings.bucket_assets,
        }
        self._config = Config(
            connect_timeout=settings.timeout_s,
            read_timeout=settings.timeout_s,
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "path"},
        )

    def _client(self) -> Any:
        s = self._settings
        if s.endpoint is None:
            raise DependencyUnavailableError("хранилище не настроено (GV_S3__ENDPOINT)")
        return self._session.client(
            "s3",
            endpoint_url=s.endpoint,
            aws_access_key_id=s.access_key.get_secret_value() if s.access_key else None,
            aws_secret_access_key=s.secret_key.get_secret_value() if s.secret_key else None,
            region_name=s.region,
            config=self._config,
        )

    def bucket(self, area: StorageArea) -> str:
        """Имя бакета области."""
        return self._buckets[area]

    async def ensure_buckets(self) -> None:
        """Создать бакеты, если их нет (идемпотентно)."""
        async with self._client() as s3:
            for name in self._buckets.values():
                try:
                    await s3.head_bucket(Bucket=name)
                except ClientError:
                    await s3.create_bucket(Bucket=name)

    async def ensure_assets_read_policy(self) -> None:
        """Выставить политику бакета ассетов (идемпотентно: каждый раз та же политика)."""
        bucket = self.bucket(StorageArea.ASSETS)
        async with self._client() as s3:
            await s3.put_bucket_policy(Bucket=bucket, Policy=assets_read_policy(bucket))

    async def put(self, area: StorageArea, key: str, data: bytes, content_type: str) -> None:
        """Записать объект."""
        async with self._client() as s3:
            await s3.put_object(
                Bucket=self.bucket(area), Key=key, Body=data, ContentType=content_type
            )

    async def get(self, area: StorageArea, key: str, *, max_bytes: int) -> bytes:
        """Прочитать объект не больше ``max_bytes`` байт."""
        async with self._client() as s3:
            try:
                response = await s3.get_object(Bucket=self.bucket(area), Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"NoSuchKey", "404"}:
                    raise GenerationError("source.missing", retryable=False) from exc
                raise
            if int(response.get("ContentLength", 0)) > max_bytes:
                raise GenerationError("source.too_large", retryable=False)
            async with response["Body"] as body:
                data: bytes = await body.read()
        if len(data) > max_bytes:
            raise GenerationError("source.too_large", retryable=False)
        return data

    async def copy(self, area: StorageArea, source_key: str, target_key: str) -> None:
        """Скопировать объект внутри бакета (серверная копия, метаданные и тип сохраняются)."""
        bucket = self.bucket(area)
        async with self._client() as s3:
            await s3.copy_object(
                Bucket=bucket,
                Key=target_key,
                CopySource={"Bucket": bucket, "Key": source_key},
                MetadataDirective="COPY",
            )

    async def list_objects(
        self, area: StorageArea, prefix: str, *, limit: int, token: str | None = None
    ) -> ObjectPage:
        """Одна страница листинга префикса (``ListObjectsV2``)."""
        params: dict[str, Any] = {
            "Bucket": self.bucket(area),
            "Prefix": prefix,
            "MaxKeys": limit,
        }
        if token is not None:
            params["ContinuationToken"] = token
        async with self._client() as s3:
            response = await s3.list_objects_v2(**params)
        items = [StoredObject(o["Key"], o["LastModified"]) for o in response.get("Contents", [])]
        next_token = response.get("NextContinuationToken") if response.get("IsTruncated") else None
        return ObjectPage(items, next_token)

    async def delete(self, area: StorageArea, keys: Sequence[str]) -> None:
        """Удалить объекты пачками."""
        if not keys:
            return
        async with self._client() as s3:
            for start in range(0, len(keys), _DELETE_BATCH):
                batch = keys[start : start + _DELETE_BATCH]
                await s3.delete_objects(
                    Bucket=self.bucket(area),
                    Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
                )


async def prepare_storage(settings: S3Settings) -> None:
    """Подготовка хранилища командой ``migrate``: бакеты и политика бакета ассетов."""
    storage = S3Storage(settings)
    await storage.ensure_buckets()
    await storage.ensure_assets_read_policy()
