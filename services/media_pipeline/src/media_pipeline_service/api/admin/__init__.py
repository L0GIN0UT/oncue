"""Admin API (/admin/v1): для админ-панели, только с правами модератора/администратора.

Единые правила admin-API: чтение — ``admin.access``; мутация — ``admin.access`` + конкретное
право, обязательные ``Idempotency-Key`` (``gv_service.IdempotencyKey``) и ``reason``
(``gv_service.Reason``), аудит в ``admin_actions``. Ключ и ответ мутации сохраняются в её же
транзакции (``gv_db.idempotency``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from gv_auth import Principal, require_perm
from gv_core.errors import InvalidInputError
from gv_service import IdempotencyKey, Reason, problem_responses
from gv_service.problems import problem
from media_pipeline_service.api.common import AssetSetItem
from media_pipeline_service.api.ugc import register_ugc_admin_routes
from media_pipeline_service.application.commands.generation import RegenerateAssetSet
from media_pipeline_service.application.commands.packages import PackageTooLargeError, SubmitPackage
from media_pipeline_service.application.commands.profiles import ReplanStaticProfiles
from media_pipeline_service.application.commands.sources import SetSourcePosition, UploadSource
from media_pipeline_service.application.commands.ugc import UgcUseCases
from media_pipeline_service.application.dto import SourceProvenance
from media_pipeline_service.application.queries.asset_sets import ListAssetSets
from media_pipeline_service.application.queries.packages import GetPackage
from media_pipeline_service.application.queries.profiles import ListProfiles
from media_pipeline_service.application.queries.sources import GetSourcePreview, ListSources
from media_pipeline_service.domain.errors import SourceTooLargeError
from media_pipeline_service.domain.packages import PackageStatus
from media_pipeline_service.domain.sources import SourceKind, SourceOrigin, SourceStatus

# Виды, которые админ загружает файлом (``text`` приходит из catalog, файлом не грузится).
UploadKind = Literal["audio", "frame", "poster", "soundtrack", "audio_clip", "cover"]
UploadOrigin = Literal["upload", "seed", "tmdb"]

_READ_CHUNK = 1024 * 1024

Viewer = Annotated[Principal, Depends(require_perm("admin.access"))]
Uploader = Annotated[Principal, Depends(require_perm("admin.access", "media.upload_source"))]
Regenerator = Annotated[Principal, Depends(require_perm("admin.access", "media.regenerate"))]


class RegenerateRequest(BaseModel):
    """Перегенерация набора."""

    reason: Reason


class ReplanRequest(BaseModel):
    """Пересоздание наборов статического профиля после правки `asset_profiles.yaml`."""

    clue_type: str = Field(max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    domain: Literal["music", "film"]
    after: UUID | None = Field(default=None, description="Курсор: `next_after` прошлой порции")
    limit: int = Field(
        default=200, ge=1, le=10_000, description="Единиц за вызов (не больше лимита сервиса)"
    )
    reason: Reason


class ReplanResponse(BaseModel):
    """Итог порции."""

    processed: int = Field(ge=0, description="Просмотрено единиц")
    scheduled: int = Field(ge=0, description="Наборов поставлено в генерацию")
    next_after: UUID | None = Field(description="Курсор следующей порции; null - домен пройден")


class UploadResponse(BaseModel):
    """Загруженный исходник и наборы, поставленные в генерацию."""

    source_media_id: UUID
    media_item_id: UUID
    kind: SourceKind
    sha256: str
    bytes: int
    mime: str
    scheduled_asset_set_ids: list[UUID]
    position: int = Field(ge=1, le=100)
    origin: SourceOrigin
    source_missing: bool = Field(description="Источник не указан")
    license_missing: bool = Field(description="Лицензия не указана")


class SourceOut(BaseModel):
    """Исходник после смены порядка и наборы, поставленные в (пере)генерацию."""

    source_media_id: UUID
    media_item_id: UUID
    kind: SourceKind
    origin: SourceOrigin
    position: int = Field(ge=1, le=100)
    status: SourceStatus
    scheduled_asset_set_ids: list[UUID]


class SourceListItem(BaseModel):
    """Исходник единицы в списке админки (хвост 3a, план этапа 3b §2.5)."""

    source_media_id: UUID
    kind: SourceKind
    position: int = Field(ge=1, le=100, description="Порядок среди исходников вида")
    origin: SourceOrigin
    status: SourceStatus
    created_at: datetime
    media_item_id: UUID | None = None
    source_url: str | None = Field(default=None, description="Откуда взят файл; null — не указан")
    license_note: str | None = Field(default=None, description="Основание; null — не указана")
    source_missing: bool = Field(description="Источник не указан")
    license_missing: bool = Field(description="Лицензия не указана")


class PackageAccepted(BaseModel):
    """Пакет принят в обработку (разбор идёт задачей)."""

    package_id: UUID
    status: PackageStatus


class PackageErrorOut(BaseModel):
    """Ошибка одного файла пакета."""

    item_key: str | None = Field(description="Ключ записи манифеста")
    path: str | None = Field(description="Путь файла в архиве")
    role: str | None
    code: str = Field(description="Машинный код: source.*, package.*")
    message: str


class PackageStatusOut(BaseModel):
    """Состояние пакета: счётчики по файлам и ошибки."""

    package_id: UUID
    status: PackageStatus
    total: int = Field(ge=0, description="Файлов в манифесте (0, пока архив не разобран)")
    done: int = Field(ge=0)
    failed: int = Field(ge=0)
    missing_source: int = Field(ge=0, description="Принятых файлов без источника")
    missing_license: int = Field(ge=0, description="Принятых файлов без лицензии")
    errors: list[PackageErrorOut]
    errors_dropped: int = Field(ge=0, description="Ошибок сверх лимита хранения (только счётчик)")
    error_code: str | None = Field(description="Почему пакет отвергнут целиком (package.*)")
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ProfileSets(BaseModel):
    """Сколько наборов профиля в каком состоянии."""

    ready: int = Field(ge=0)
    pending: int = Field(ge=0)
    failed: int = Field(ge=0)


class ProfileOut(BaseModel):
    """Профиль ассетов: статический (``asset_profiles.yaml``) или динамический (ADR 0025)."""

    clue_type: str
    profile_key: str | None = Field(
        description="null у динамического профиля (в наборах и AssetsReady — dyn_<16 hex хэша>)"
    )
    params_hash: str
    origin: Literal["static", "dynamic"]
    status: Literal["active", "backfilling", "rejected"]
    sets: ProfileSets
    domains: list[Literal["music", "film"]] = Field(default_factory=list)
    error_code: str | None = Field(
        default=None,
        description=(
            "Почему отклонён динамический профиль: profile.invalid, profile.unknown_clue_type, "
            "profile.hash_mismatch, profile.limit_reached, profile.params_too_large"
        ),
    )


class SourcePositionRequest(BaseModel):
    """Порядок исходника своего вида (кадры ``frame_sequence``: 1 — самый сложный)."""

    position: int = Field(ge=1, le=100)
    reason: Reason


async def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Прочитать файл не больше лимита (чтение порциями, без загрузки «бомбы» в память)."""
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(_READ_CHUNK):
        size += len(chunk)
        if size > max_bytes:
            raise SourceTooLargeError("файл больше лимита", details={"max_bytes": max_bytes})
        chunks.append(chunk)
    return b"".join(chunks)


def build_admin_router(
    *,
    upload: UploadSource,
    set_position: SetSourcePosition,
    regenerate: RegenerateAssetSet,
    replan: ReplanStaticProfiles,
    list_sets: ListAssetSets,
    list_profiles: ListProfiles,
    list_sources: ListSources,
    get_source_preview: GetSourcePreview,
    submit_package: SubmitPackage,
    get_package: GetPackage,
    ugc: UgcUseCases,
    max_upload_bytes: int,
    kind_upload_limits: Mapping[SourceKind, int] | None = None,
) -> APIRouter:
    """Ручки админки: загрузка исходников, перегенерация, просмотр наборов."""
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    @router.post(
        "/sources",
        status_code=201,
        response_model=UploadResponse,
        summary="Загрузить исходник (аудио, кадр, постер, обложка, саундтрек, звук) для единицы",
        description=(
            "origin, source_url и license_note необязательны (ADR 0072): origin по умолчанию "
            "upload, пустые источник и лицензия отмечаются в ответе (source_missing, "
            "license_missing). position — порядок среди исходников вида "
            "(кадры frame_sequence: 1 — самый сложный)."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 503),
    )
    async def upload_source(
        *,
        principal: Uploader,
        key: IdempotencyKey,
        media_item_id: Annotated[UUID, Form()],
        kind: Annotated[UploadKind, Form()],
        reason: Annotated[Reason, Form()],
        file: Annotated[UploadFile, File(description="Файл исходника")],
        position: Annotated[int, Form(ge=1, le=100)] = 1,
        origin: Annotated[UploadOrigin | None, Form()] = None,
        source_url: Annotated[str | None, Form(min_length=1, max_length=2000)] = None,
        license_note: Annotated[str | None, Form(min_length=1, max_length=500)] = None,
    ) -> UploadResponse:
        limit = min(
            max_upload_bytes, (kind_upload_limits or {}).get(SourceKind(kind), max_upload_bytes)
        )
        data = await _read_limited(file, limit)
        result = await upload.execute(
            media_item_id=media_item_id,
            kind=SourceKind(kind),
            data=data,
            actor_id=principal.user_id,
            reason=reason,
            idempotency_key=key,
            position=position,
            provenance=SourceProvenance(
                origin=SourceOrigin(origin) if origin else None,
                source_url=source_url,
                license_note=license_note,
            ),
        )
        return UploadResponse(
            source_media_id=result.source_media_id,
            media_item_id=result.media_item_id,
            kind=result.kind,
            sha256=result.sha256,
            bytes=result.size_bytes,
            mime=result.mime,
            scheduled_asset_set_ids=list(result.scheduled_asset_set_ids),
            position=result.position,
            origin=result.origin,
            source_missing=result.source_missing,
            license_missing=result.license_missing,
        )

    @router.post(
        "/packages",
        status_code=202,
        response_model=PackageAccepted,
        summary="Загрузить пакет исходников архивом (zip + manifest.json)",
        description=(
            "Тело запроса - zip-архив (`application/zip`), причина - в query `reason`. "
            "Архив пишется в хранилище потоком, ответ 202 с `package_id` приходит сразу, "
            "разбор идёт задачей; статус - `GET /admin/v1/packages/{id}`. В корне архива "
            "`manifest.json` по схеме `contracts/packages/media_package_manifest.v1.json`. "
            "Каждый файл проходит тот же приём, что и `POST /admin/v1/sources`; один плохой "
            "файл не останавливает остальные. Лимиты размера архива, числа файлов, "
            "распакованного размера и степени сжатия - конфиг `GV_MEDIA__PACKAGE_*`. "
            "Повтор с тем же `Idempotency-Key` и архивом отвечает тем же `package_id`; тот же "
            "архив с новым ключом, от того же админа и с той же причиной, исходники повторно "
            "не создаёт (иначе создаст: идемпотентность файлов привязана к актору и причине). "
            "Ошибки: 413 "
            "`package.archive_too_large`, 422 `package.not_zip`."
        ),
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
        responses=problem_responses(401, 403, 409, 413, 422, 503),
    )
    async def submit_package_archive(
        request: Request,
        principal: Uploader,
        key: IdempotencyKey,
        reason: Reason,
    ) -> PackageAccepted | Response:
        declared = request.headers.get("content-length")
        try:
            result = await submit_package.execute(
                body=request.stream(),
                declared_length=int(declared) if declared and declared.isdigit() else None,
                actor_id=principal.user_id,
                reason=reason,
                idempotency_key=key,
            )
        except PackageTooLargeError as exc:
            return problem(
                413,
                exc.code,
                "Архив больше допустимого размера",
                extra={"details": exc.details},
            )
        return PackageAccepted(package_id=result.package_id, status=result.status)

    @router.get(
        "/sources/{source_media_id}/preview",
        response_class=Response,
        summary="Картинка каталожного исходника (обложка, постер, кадр)",
        description=(
            "Нормализованная копия читается из хранилища по ключу сервиса, адресов S3 в ответе "
            "нет. Заголовки: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, "
            "`Content-Disposition: inline`. Нет исходника, он не изображение, не готов или "
            "принадлежит сборнику - 404 `source.not_found`."
        ),
        responses={
            **problem_responses(401, 403, 404),
            200: {
                "description": "Изображение (PNG: нормализованная копия исходника)",
                "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
            },
        },
    )
    async def source_preview(source_media_id: UUID, principal: Viewer) -> Response:
        del principal  # доступ проверяет зависимость Viewer
        result = await get_source_preview.execute(source_media_id)
        return Response(
            content=result.data,
            media_type=result.content_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "inline",
            },
        )

    @router.get(
        "/packages/{package_id}",
        response_model=PackageStatusOut,
        summary="Статус пакета: счётчики и ошибки по файлам",
        responses=problem_responses(401, 403, 404, 422),
    )
    async def package_status(package_id: UUID, _: Viewer) -> PackageStatusOut:
        package = await get_package.execute(package_id)
        return PackageStatusOut(
            package_id=package.id,
            status=package.status,
            total=package.total,
            done=package.done,
            failed=package.failed,
            missing_source=package.missing_source,
            missing_license=package.missing_license,
            errors=[
                PackageErrorOut(
                    item_key=e.item_key, path=e.path, role=e.role, code=e.code, message=e.message
                )
                for e in package.errors
            ],
            errors_dropped=package.errors_dropped,
            error_code=package.error_code,
            error_message=package.error_message,
            created_at=package.created_at,
            started_at=package.started_at,
            finished_at=package.finished_at,
        )

    @router.post(
        "/asset-sets/{asset_set_id}/regenerate",
        status_code=202,
        response_model=AssetSetItem,
        summary="Перегенерировать набор ассетов",
        responses=problem_responses(401, 403, 404, 409, 410, 422),
    )
    async def regenerate_asset_set(
        asset_set_id: UUID,
        body: RegenerateRequest,
        principal: Regenerator,
        key: IdempotencyKey,
    ) -> AssetSetItem:
        view = await regenerate.execute(
            asset_set_id, actor_id=principal.user_id, reason=body.reason, idempotency_key=key
        )
        return AssetSetItem.of(view)

    @router.post(
        "/asset-sets/replan",
        response_model=ReplanResponse,
        summary="Досоздать наборы статического профиля для уже известных единиц (порция)",
        description=(
            "После правки `config/asset_profiles.yaml` меняется `params_hash`: у единиц, "
            "загруженных раньше, нет наборов под новый хэш. Вызов планирует недостающие наборы "
            "порции единиц домена (существующие не трогает); повторять с `after = next_after`, "
            "пока `next_after` не `null`. Старые наборы остаются (по ним доигрываются начатые "
            "сессии)."
        ),
        responses=problem_responses(401, 403, 409, 422),
    )
    async def replan_asset_sets(
        body: ReplanRequest, principal: Regenerator, key: IdempotencyKey
    ) -> ReplanResponse:
        batch = await replan.execute(
            clue_type=body.clue_type,
            domain=body.domain,
            after=body.after,
            limit=body.limit,
            actor_id=principal.user_id,
            reason=body.reason,
            idempotency_key=key,
        )
        return ReplanResponse(
            processed=batch.processed, scheduled=batch.scheduled, next_after=batch.next_after
        )

    @router.get(
        "/profiles",
        response_model=list[ProfileOut],
        summary="Профили ассетов и готовность их наборов",
        responses=problem_responses(401, 403, 422),
    )
    async def admin_profiles(
        _: Viewer,
        clue_type: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z][a-z0-9_]*$")] = None,
        params_hash: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
    ) -> list[ProfileOut]:
        views = await list_profiles.execute(clue_type, params_hash)
        return [
            ProfileOut(
                clue_type=v.clue_type,
                profile_key=v.profile_key,
                params_hash=v.params_hash,
                origin=v.origin.value,
                status=v.status,
                sets=ProfileSets(ready=v.ready, pending=v.pending, failed=v.failed),
                domains=list(v.domains),
                error_code=v.error_code,
            )
            for v in views
        ]

    @router.put(
        "/sources/{source_media_id}/position",
        response_model=SourceOut,
        summary="Изменить порядок исходника",
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def set_source_position(
        source_media_id: UUID,
        body: SourcePositionRequest,
        principal: Uploader,
        key: IdempotencyKey,
    ) -> SourceOut:
        view = await set_position.execute(
            source_media_id,
            position=body.position,
            actor_id=principal.user_id,
            reason=body.reason,
            idempotency_key=key,
        )
        return SourceOut(
            source_media_id=view.source_media_id,
            media_item_id=view.media_item_id,
            kind=view.kind,
            origin=view.origin,
            position=view.position,
            status=SourceStatus(view.status),
            scheduled_asset_set_ids=list(view.scheduled_asset_set_ids),
        )

    @router.get(
        "/sources",
        response_model=list[SourceListItem],
        summary="Исходники единицы с порядком (для карточки в админке)",
        description=(
            "По виду, затем по `position` (кадры `frame_sequence`: 1 — самый сложный). "
            "Без ссылок на объекты S3. Перестановка — `PUT /admin/v1/sources/{id}/position`. "
            "Вместо `media_item_id` можно передать `without_attribution=true`: файловые "
            "исходники всех единиц без источника или лицензии (страницы по `after`)."
        ),
        responses=problem_responses(401, 403, 422),
    )
    async def admin_sources(
        _: Viewer,
        *,
        media_item_id: UUID | None = None,
        without_attribution: bool = False,
        after: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[SourceListItem]:
        if media_item_id is not None and not without_attribution:
            views = await list_sources.execute(media_item_id)
        elif media_item_id is None and without_attribution:
            views = await list_sources.unattributed(limit=limit, after_id=after)
        else:
            raise InvalidInputError(
                "нужен ровно один из media_item_id и without_attribution=true",
                code="request.validation_failed",
            )
        return [
            SourceListItem(
                source_media_id=v.source_media_id,
                kind=v.kind,
                position=v.position,
                origin=v.origin,
                status=v.status,
                created_at=v.created_at,
                media_item_id=v.media_item_id,
                source_url=v.source_url,
                license_note=v.license_note,
                source_missing=v.source_missing,
                license_missing=v.license_missing,
            )
            for v in views
        ]

    @router.get(
        "/asset-sets",
        response_model=list[AssetSetItem],
        summary="Наборы ассетов единицы (для карточки в админке)",
        responses=problem_responses(401, 403, 422),
    )
    async def admin_asset_sets(media_item_id: UUID, _: Viewer) -> list[AssetSetItem]:
        return [AssetSetItem.of(v) for v in await list_sets.execute(media_item_id)]

    register_ugc_admin_routes(router, ugc)
    return router
