"""Исходники админом: загрузка (``POST /admin/v1/sources``) и порядок
(``PUT /admin/v1/sources/{id}/position``)."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import ConflictError, DependencyUnavailableError, InvalidInputError
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent
from media_pipeline_service.application.commands.catalog import PlanAssetSets, PlannedSets
from media_pipeline_service.application.dto import SourceProvenance, SourceView, UploadResult
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import (
    AdminAction,
    MediaUnitOfWork,
    ObjectStorage,
    StorageArea,
    UnitOfWorkFactory,
)
from media_pipeline_service.application.profiles import ProfileDirectory
from media_pipeline_service.application.sources import (
    SourceIngestor,
    StoredSource,
    delete_quietly,
)
from media_pipeline_service.domain.errors import (
    MediaItemNotFoundError,
    SourceNotFoundError,
)
from media_pipeline_service.domain.sources import (
    SourceKind,
    SourceMedia,
    SourceOrigin,
    SourceStatus,
)

log = structlog.get_logger(__name__)


class UploadSource:
    """Принять файл, перекодировать, сохранить и запланировать (пере)генерацию наборов.

    Происхождение (``origin``, ``source_url``, ``license_note``) необязательно в любой среде
    (ADR 0072): пустое не блокирует загрузку, ``origin`` по умолчанию ``upload``. Исходники
    без источника или лицензии помечаются в ответах и находятся фильтром списка.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        ingestor: SourceIngestor,
        profiles: ProfileDirectory,
        plan: PlanAssetSets,
        *,
        storage: ObjectStorage,
    ) -> None:
        self._storage = storage
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
        idempotency_key: str,
        position: int = 1,
        provenance: SourceProvenance | None = None,
    ) -> UploadResult:
        """Загрузить (идемпотентно по ``(actor_id, idempotency_key)``).

        Args:
            position: Порядок среди исходников вида (кадры ``frame_sequence``).
            provenance: Происхождение; все поля необязательны (ADR 0072).

        Ключ и ответ сохраняются в той же транзакции, что и исходник, аудит и планирование
        наборов: сбой до коммита не оставляет ключа (повтор выполнится заново), после
        коммита повтор получает сохранённый ответ без повторной обработки файла.

        Raises:
            MediaItemNotFoundError: единица не известна сервису.
            ConflictError: единица заблокирована (``media_item.blocked``); ключ уже
                использован с другим запросом (``idempotency.key_reused``) или запрос с ним
                выполняется параллельно (``idempotency.in_progress``).
            InvalidInputError: вид исходника не используется профилями домена единицы,
                файл не того типа, больше лимита или не декодируется.
        """
        provenance = provenance or SourceProvenance()
        # Пустая строка = «не указано»: в БД всегда NULL, без значений-заглушек.
        provenance = SourceProvenance(
            origin=provenance.origin,
            source_url=provenance.source_url or None,
            license_note=provenance.license_note or None,
        )
        origin = provenance.origin or SourceOrigin.UPLOAD
        fingerprint = request_fingerprint(
            "source.upload",
            media_item_id,
            {
                "kind": kind.value,
                "reason": reason,
                "sha256": hashlib.sha256(data).hexdigest(),
                "position": position,
                "origin": origin.value,
                "source_url": provenance.source_url,
                "license_note": provenance.license_note,
            },
        )
        async with self._uow() as uow:
            # Повтор отвечаем до перекодирования файла: ffmpeg/Pillow и S3 не нужны.
            existing = await uow.idempotency.find(actor_id, idempotency_key)
            if existing is None:
                await self._check_applicable(uow, media_item_id, kind)
        if existing is not None:
            if existing.request_hash != fingerprint:
                raise ConflictError(
                    "Idempotency-Key уже использован с другим запросом",
                    code="idempotency.key_reused",
                )
            return UploadResult.from_json(existing.response)
        try:
            stored = await self._ingestor.ingest(kind, data)
        except GenerationError as exc:
            if exc.retryable:
                raise DependencyUnavailableError("обработка файла временно недоступна") from exc
            raise InvalidInputError("файл не удалось обработать", code=exc.code) from exc

        planned = PlannedSets((), ())
        executed = False
        try:
            async with self._uow() as uow:

                async def perform() -> dict[str, Any]:
                    nonlocal planned
                    # Состояние могло измениться, пока обрабатывали файл.
                    await self._check_applicable(uow, media_item_id, kind)
                    source = self._new_source(
                        media_item_id,
                        kind,
                        stored,
                        position=position,
                        origin=origin,
                        provenance=provenance,
                    )
                    await uow.sources.add(source)
                    await uow.audit.record(
                        AdminAction(
                            actor_id=actor_id,
                            action="source.upload",
                            target_id=media_item_id,
                            reason=reason,
                            at=source.created_at,
                            details={
                                "source_media_id": str(source.id),
                                "kind": kind.value,
                                "position": position,
                                "origin": origin.value,
                                "source_url": provenance.source_url,
                                "license_note": provenance.license_note,
                            },
                        )
                    )
                    planned = await self._plan.plan_in(uow, media_item_id)
                    return UploadResult(
                        source_media_id=source.id,
                        media_item_id=media_item_id,
                        kind=kind,
                        sha256=stored.sha256,
                        size_bytes=stored.size_bytes,
                        mime=stored.mime,
                        scheduled_asset_set_ids=planned.asset_set_ids,
                        position=position,
                        origin=origin,
                        source_missing=not provenance.source_url,
                        license_missing=not provenance.license_note,
                    ).to_json()

                response, executed_now = await run_idempotent(
                    uow.idempotency,
                    actor_id,
                    idempotency_key,
                    fingerprint,
                    perform,
                    status_code=201,
                )
                await uow.commit()
                executed = executed_now
        finally:
            if not executed:
                # Мутация не состоялась (ошибка или параллельный повтор успел раньше):
                # перекодированный объект ни на что не ссылается.
                await delete_quietly(self._storage, StorageArea.SOURCES, [stored.object_key])
        await delete_quietly(self._storage, StorageArea.ASSETS, planned.stale_keys)
        result = UploadResult.from_json(response)
        log.info(
            "source.uploaded",
            media_item_id=str(media_item_id),
            kind=kind.value,
            size_bytes=result.size_bytes,
            scheduled=len(result.scheduled_asset_set_ids),
        )
        return result

    async def _check_applicable(
        self, uow: MediaUnitOfWork, media_item_id: UUID, kind: SourceKind
    ) -> None:
        state = await uow.items.get(media_item_id)
        if state is None:
            raise MediaItemNotFoundError("медиа-единица не найдена")
        if state.is_blocked:
            raise ConflictError("единица заблокирована", code="media_item.blocked")
        if state.domain is None or not any(
            p.source_kind is kind for p in await self._profiles.for_domain(uow, state.domain)
        ):
            raise InvalidInputError(
                f"исходник вида {kind.value} не используется для домена единицы",
                code="source.kind_not_applicable",
            )

    def _new_source(
        self,
        media_item_id: UUID,
        kind: SourceKind,
        stored: StoredSource,
        *,
        position: int,
        origin: SourceOrigin,
        provenance: SourceProvenance,
    ) -> SourceMedia:
        source = SourceMedia(
            uuid7(),
            media_item_id=media_item_id,
            kind=kind,
            origin=origin,
            uri=None,
            created_at=self._clock.now(),
            status=SourceStatus.REGISTERED,
            position=position,
            source_url=provenance.source_url,
            license_note=provenance.license_note,
        )
        source.mark_stored(
            object_key=stored.object_key,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            mime=stored.mime,
        )
        return source


class SetSourcePosition:
    """Сменить порядок исходника среди своего вида и перепланировать наборы единицы.

    Новый порядок кадров меняет состав исходников ``frame_sequence`` — ``PlanAssetSets``
    перегенерирует затронутые наборы (новое поколение).
    """

    def __init__(
        self, uow: UnitOfWorkFactory, clock: Clock, plan: PlanAssetSets, *, storage: ObjectStorage
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._plan = plan
        self._storage = storage

    async def execute(
        self,
        source_media_id: UUID,
        *,
        position: int,
        actor_id: UUID,
        reason: str,
        idempotency_key: str,
    ) -> SourceView:
        """Сменить порядок (идемпотентно по ``(actor_id, idempotency_key)``).

        Raises:
            SourceNotFoundError: нет исходника.
            ConflictError: единица заблокирована; ключ идемпотентности переиспользован.
        """
        fingerprint = request_fingerprint(
            "source.position", source_media_id, {"position": position, "reason": reason}
        )
        planned = PlannedSets((), ())
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                nonlocal planned
                source = await uow.sources.get(source_media_id)
                if source is None:
                    raise SourceNotFoundError("исходник не найден")
                state = await uow.items.get(source.media_item_id)
                if state is not None and state.is_blocked:
                    raise ConflictError("единица заблокирована", code="media_item.blocked")
                previous = source.position
                source.move_to(position)
                await uow.sources.save(source)
                await uow.audit.record(
                    AdminAction(
                        actor_id=actor_id,
                        action="source.position",
                        target_id=source.id,
                        reason=reason,
                        at=self._clock.now(),
                        details={"from": previous, "to": position},
                    )
                )
                planned = await self._plan.plan_in(uow, source.media_item_id)
                return SourceView(
                    source_media_id=source.id,
                    media_item_id=source.media_item_id,
                    kind=source.kind,
                    origin=source.origin,
                    position=source.position,
                    status=source.status.value,
                    scheduled_asset_set_ids=planned.asset_set_ids,
                ).to_json()

            response, executed = await run_idempotent(
                uow.idempotency, actor_id, idempotency_key, fingerprint, perform
            )
            await uow.commit()
        if executed:
            await delete_quietly(self._storage, StorageArea.ASSETS, planned.stale_keys)
            log.info(
                "source.position_changed", source_media_id=str(source_media_id), position=position
            )
        return SourceView.from_json(response)
