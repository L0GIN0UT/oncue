"""Выдача подписанной ссылки на уровень набора (``POST /internal/v1/asset-links``)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import InvalidInputError, RateLimitedError
from media_pipeline_service.application.dto import CoverLink, IssuedLink
from media_pipeline_service.application.ports import LinkRateLimiter, UnitOfWorkFactory, UrlSigner
from media_pipeline_service.domain.asset_set import AssetSetStatus
from media_pipeline_service.domain.errors import AssetBlockedError, AssetSetNotFoundError
from media_pipeline_service.domain.links import CoverLinkPolicy, LinkPolicy

log = structlog.get_logger(__name__)

COVER_CLUE_TYPE = "artwork"
"""Тип загадки набора с картинкой ответа (ADR 0073): обложка альбома или постер."""

COVER_SUBJECT = "covers"
"""Субъект журнала выдачи для обложек карточек (в БД уходит только sha256)."""


class IssueAssetLink:
    """Выдать короткоживущую ссылку (ADR 0012).

    Порядок проверок задаёт контракт ошибок: 404 → 410 → 409 → 422 → 429. Лимит считается
    только для допустимых запросов, чтобы ошибки клиента не съедали квоту.

    Журнал выдачи здесь не чистится: это делает периодическая задача
    ``media.link_issuance.purge`` (``PurgeLinkJournal``), а не горячий путь выдачи.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        signer: UrlSigner,
        limiter: LinkRateLimiter,
        policy: LinkPolicy,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._signer = signer
        self._limiter = limiter
        self._policy = policy

    async def execute(
        self, asset_set_id: UUID, level: int, subject: str, *, client_id: str | None = None
    ) -> IssuedLink:
        """Выдать ссылку на уровень ``level`` для субъекта ``subject``.

        Args:
            client_id: Сервис-получатель (из сервисного токена) — пишется в журнал выдачи.
        """
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
                client_id=client_id,
            )
            await uow.commit()
        return IssuedLink(url=url, expires_at=expires_at, mime=asset.mime, level=level)


class PurgeLinkJournal:
    """Удалить записи журнала выдачи старше срока хранения (задача ``media.link_issuance.purge``).

    Удаляет порциями по ``batch`` строк, каждая — в своей транзакции: долгая очистка не
    держит блокировки на весь журнал. Идемпотентна: повтор удаляет только оставшееся.
    """

    def __init__(
        self, uow: UnitOfWorkFactory, clock: Clock, *, retention: timedelta, batch: int
    ) -> None:
        if batch < 1:
            raise ValueError("batch должен быть положительным")
        self._uow = uow
        self._clock = clock
        self._retention = retention
        self._batch = batch

    async def execute(self) -> int:
        """Очистить; вернуть число удалённых записей."""
        cutoff = self._clock.now() - self._retention
        total = 0
        while True:
            async with self._uow() as uow:
                deleted = await uow.links.purge_older_than(cutoff, limit=self._batch)
                await uow.commit()
            total += deleted
            if deleted < self._batch:
                break
        log.info("link_issuance.purged", deleted=total)
        return total


class IssueCoverLinks:
    """Пакетная выдача ссылок на обложки единиц (``POST /internal/v1/cover-links``).

    Для карточек каталога: картинка ответа не секретна, но остаётся за подписью gateway.
    Берётся каталожный набор ``artwork`` (не наборы сборников), уровень 1. Единицы без
    готового набора, заблокированные и неизвестные молча пропускаются — клиент оставит
    пресет. Лимит выдачи на единицу не применяется: выдача идёт только доверенному сервису
    (область ``assets.cover_link``), а срок окна (``CoverLinkPolicy``) делает ссылку
    стабильной и кэшируемой на стороне клиента.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        signer: UrlSigner,
        policy: CoverLinkPolicy,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._signer = signer
        self._policy = policy

    async def execute(
        self, media_item_ids: Sequence[UUID], *, client_id: str | None = None
    ) -> list[CoverLink]:
        """Выдать ссылки на обложки; порядок результата — порядок первых вхождений id.

        Raises:
            InvalidInputError: пустой список или больше ``batch_max`` единиц.
        """
        ids = list(dict.fromkeys(media_item_ids))
        if not ids:
            raise InvalidInputError("список единиц пуст", code="cover.empty_batch")
        if len(ids) > self._policy.batch_max:
            raise InvalidInputError(
                "слишком много единиц за один запрос",
                code="cover.batch_too_large",
                details={"max": self._policy.batch_max, "given": len(ids)},
            )
        now = self._clock.now()
        expires_at = self._policy.expires_at(now)
        links: list[CoverLink] = []
        async with self._uow() as uow:
            states = await uow.items.get_many(ids)
            for item_id in ids:
                state = states.get(item_id)
                if state is None or state.is_blocked:
                    continue
                # scope=None — только каталожные наборы: обложки чужих сборников не выдаём.
                sets = await uow.asset_sets.list_for_item(item_id, COVER_CLUE_TYPE, scope=None)
                ready = [s for s in sets if s.status is AssetSetStatus.READY]
                if not ready:
                    continue
                asset = ready[-1].level(1)
                links.append(
                    CoverLink(
                        media_item_id=item_id,
                        url=self._signer.sign(asset.object_key, expires_at),
                        expires_at=expires_at,
                        mime=asset.mime,
                    )
                )
                await uow.links.record(
                    asset_set_id=ready[-1].id,
                    level=1,
                    subject_hash=LinkPolicy.subject_hash(COVER_SUBJECT),
                    issued_at=now,
                    expires_at=expires_at,
                    client_id=client_id,
                )
            await uow.commit()
        return links
