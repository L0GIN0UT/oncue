"""Выдача подписанной ссылки на уровень набора (``POST /internal/v1/asset-links``)."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import RateLimitedError
from media_pipeline_service.application.dto import IssuedLink
from media_pipeline_service.application.ports import LinkRateLimiter, UnitOfWorkFactory, UrlSigner
from media_pipeline_service.domain.errors import AssetBlockedError, AssetSetNotFoundError
from media_pipeline_service.domain.links import LinkPolicy

log = structlog.get_logger(__name__)

_PURGE_BATCH = 1000


class IssueAssetLink:
    """Выдать короткоживущую ссылку (ADR 0012).

    Порядок проверок задаёт контракт ошибок: 404 → 410 → 409 → 422 → 429. Лимит считается
    только для допустимых запросов, чтобы ошибки клиента не съедали квоту.

    Журнал выдачи чистится здесь же, не чаще ``purge_interval``: отдельного планировщика
    в этапе 1 нет, а удаление пачкой по индексу ``issued_at`` дешёвое.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        signer: UrlSigner,
        limiter: LinkRateLimiter,
        policy: LinkPolicy,
        *,
        retention: timedelta,
        purge_interval: timedelta = timedelta(minutes=10),
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._signer = signer
        self._limiter = limiter
        self._policy = policy
        self._retention = retention
        self._purge_interval = purge_interval
        self._next_purge_at: datetime | None = None

    async def execute(self, asset_set_id: UUID, level: int, subject: str) -> IssuedLink:
        """Выдать ссылку на уровень ``level`` для субъекта ``subject``."""
        async with self._uow() as uow:
            aggregate = await uow.asset_sets.get(asset_set_id)
            if aggregate is None:
                raise AssetSetNotFoundError("набор ассетов не найден")
            item = await uow.items.get(aggregate.media_item_id)
            if item is not None and item.is_blocked:
                raise AssetBlockedError("единица заблокирована")
            asset = aggregate.level(level)
            count = await self._limiter.hit(
                LinkPolicy.rate_key(subject, asset_set_id, level), self._policy.window_s
            )
            if count > self._policy.max_per_window:
                log.warning("asset_link.rate_limited", asset_set_id=str(asset_set_id), level=level)
                raise RateLimitedError(
                    "слишком много ссылок на этот уровень", code="asset.rate_limited"
                )
            now = self._clock.now()
            expires_at = self._policy.expires_at(now)
            url = self._signer.sign(asset.object_key, expires_at)
            await uow.links.record(
                asset_set_id=asset_set_id,
                level=level,
                subject_hash=LinkPolicy.subject_hash(subject),
                issued_at=now,
                expires_at=expires_at,
            )
            if self._next_purge_at is None or now >= self._next_purge_at:
                self._next_purge_at = now + self._purge_interval
                await uow.links.purge_older_than(now - self._retention, limit=_PURGE_BATCH)
            await uow.commit()
        return IssuedLink(url=url, expires_at=expires_at, mime=asset.mime, level=level)
