"""Загрузка исходника админом (``POST /admin/v1/sources``)."""

from __future__ import annotations

from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import ConflictError, DependencyUnavailableError, InvalidInputError
from gv_core.ids import uuid7
from media_pipeline_service.application.commands.catalog import PlanAssetSets
from media_pipeline_service.application.dto import UploadResult
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import AdminAction, UnitOfWorkFactory
from media_pipeline_service.application.sources import SourceIngestor
from media_pipeline_service.domain.errors import MediaItemNotFoundError
from media_pipeline_service.domain.profiles import AssetProfileCatalog
from media_pipeline_service.domain.sources import (
    SourceKind,
    SourceMedia,
    SourceOrigin,
    SourceStatus,
)

log = structlog.get_logger(__name__)


class UploadSource:
    """Принять файл, перекодировать, сохранить и запланировать (пере)генерацию наборов."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        ingestor: SourceIngestor,
        profiles: AssetProfileCatalog,
        plan: PlanAssetSets,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._ingestor = ingestor
        self._profiles = profiles
        self._plan = plan

    async def execute(
        self,
        *,
        media_item_id: UUID,
        kind: SourceKind,
        data: bytes,
        actor_id: UUID,
        reason: str,
    ) -> UploadResult:
        """Загрузить.

        Raises:
            MediaItemNotFoundError: единица не известна сервису.
            ConflictError: единица заблокирована (``media_item.blocked``).
            InvalidInputError: вид исходника не используется профилями домена единицы,
                файл не того типа, больше лимита или не декодируется.
        """
        async with self._uow() as uow:
            state = await uow.items.get(media_item_id)
        if state is None:
            raise MediaItemNotFoundError("медиа-единица не найдена")
        if state.is_blocked:
            raise ConflictError("единица заблокирована", code="media_item.blocked")
        if state.domain is None or not any(
            p.source_kind is kind for p in self._profiles.for_domain(state.domain)
        ):
            raise InvalidInputError(
                f"исходник вида {kind.value} не используется для домена единицы",
                code="source.kind_not_applicable",
            )
        try:
            stored = await self._ingestor.ingest(kind, data)
        except GenerationError as exc:
            if exc.retryable:
                raise DependencyUnavailableError("обработка файла временно недоступна") from exc
            raise InvalidInputError("файл не удалось обработать", code=exc.code) from exc

        source_id = uuid7()
        now = self._clock.now()
        source = SourceMedia(
            source_id,
            media_item_id=media_item_id,
            kind=kind,
            origin=SourceOrigin.UPLOAD,
            uri=None,
            created_at=now,
            status=SourceStatus.REGISTERED,
        )
        source.mark_stored(
            object_key=stored.object_key,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            mime=stored.mime,
        )
        async with self._uow() as uow:
            await uow.sources.add(source)
            await uow.audit.record(
                AdminAction(
                    actor_id=actor_id,
                    action="source.upload",
                    target_id=media_item_id,
                    reason=reason,
                    at=now,
                    details={"source_media_id": str(source_id), "kind": kind.value},
                )
            )
            await uow.commit()
        scheduled = await self._plan.execute(media_item_id)
        log.info(
            "source.uploaded",
            media_item_id=str(media_item_id),
            kind=kind.value,
            size_bytes=stored.size_bytes,
            scheduled=len(scheduled),
        )
        return UploadResult(
            source_media_id=source_id,
            media_item_id=media_item_id,
            kind=kind,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            mime=stored.mime,
            scheduled_asset_set_ids=tuple(scheduled),
        )
