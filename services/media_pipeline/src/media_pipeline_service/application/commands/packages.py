"""Пакетная загрузка исходников архивом: приём (``POST /admin/v1/packages``) и разбор задачей.

Приём только кладёт архив в S3 потоком и ставит задачу ``media.package.process``; разбор идёт
в воркере. Каждый файл пакета проходит через тот же ``UploadSource``, что и ручная загрузка
(сигнатура, перекодирование, планирование наборов), - новой логики приёма исходников здесь нет.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import AppError, DependencyUnavailableError, InvalidInputError
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent
from gv_messaging.ports import TaskQueue
from media_pipeline_service.application.archive import ArchiveLimits, SafeArchive, open_archive
from media_pipeline_service.application.commands.generation import TaskNotVisibleError
from media_pipeline_service.application.commands.sources import UploadSource
from media_pipeline_service.application.dto import PACKAGE_TASK, SourceProvenance
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import (
    AdminAction,
    ObjectStorage,
    StorageArea,
    TokenSource,
    UnitOfWorkFactory,
)
from media_pipeline_service.application.sources import delete_quietly
from media_pipeline_service.domain.packages import (
    MANIFEST_NAME,
    PACKAGE_PREFIX,
    ImportPackage,
    ManifestItem,
    PackageManifest,
    PackageRejectedError,
    PackageStatus,
    package_object_key,
    parse_manifest,
)

log = structlog.get_logger(__name__)

_ZIP_MAGIC = b"PK\x03\x04"


class PackageTooLargeError(InvalidInputError):
    """Архив больше лимита (API отвечает 413)."""

    default_code = "package.archive_too_large"


class PackageSubmitted:
    """Ответ приёма: идентификатор пакета и состояние (для повтора - сохранённый ответ)."""

    def __init__(self, package_id: UUID, status: PackageStatus) -> None:
        self.package_id = package_id
        self.status = status

    def to_json(self) -> dict[str, Any]:
        """JSON для хранения ответа идемпотентной мутации."""
        return {"package_id": str(self.package_id), "status": self.status.value}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PackageSubmitted:
        """Из сохранённого ответа."""
        return cls(UUID(data["package_id"]), PackageStatus(data["status"]))


class SubmitPackage:
    """Принять архив потоком, сохранить в бакет исходников и поставить задачу разбора.

    Архив не попадает в память целиком: тело идёт в S3 частями (multipart), размер и
    сигнатура zip проверяются на лету. Повтор с тем же ``Idempotency-Key`` и тем же архивом
    возвращает тот же ``package_id``; тот же ключ с другим архивом - 409.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        storage: ObjectStorage,
        queue: TaskQueue,
        tokens: TokenSource,
        *,
        limits: ArchiveLimits,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._storage = storage
        self._queue = queue
        self._tokens = tokens
        self._limits = limits

    async def execute(
        self,
        *,
        body: AsyncIterator[bytes],
        declared_length: int | None,
        actor_id: UUID,
        reason: str,
        idempotency_key: str,
    ) -> PackageSubmitted:
        """Принять пакет.

        Args:
            body: Тело запроса порциями.
            declared_length: ``Content-Length``, если клиент его прислал (ранний отказ).

        Raises:
            PackageTooLargeError: архив больше ``max_archive_bytes``.
            PackageRejectedError: тело не начинается с сигнатуры zip.
            ConflictError: ``Idempotency-Key`` уже использован с другим архивом.
        """
        if declared_length is not None and declared_length > self._limits.max_archive_bytes:
            raise PackageTooLargeError(
                "архив больше лимита", details={"max_bytes": self._limits.max_archive_bytes}
            )
        key = package_object_key(self._tokens.token_hex())
        digest = hashlib.sha256()
        counter = _Counter()
        await self._storage.put_stream(
            StorageArea.SOURCES,
            key,
            self._guard(body, digest, counter),
            "application/zip",
        )
        sha256 = digest.hexdigest()
        executed = False
        try:
            async with self._uow() as uow:

                async def perform() -> dict[str, Any]:
                    package = ImportPackage(
                        uuid7(),
                        actor_id=actor_id,
                        reason=reason,
                        archive_key=key,
                        archive_sha256=sha256,
                        archive_bytes=counter.size,
                        created_at=self._clock.now(),
                    )
                    await uow.packages.add(package)
                    await uow.audit.record(
                        AdminAction(
                            actor_id=actor_id,
                            action="package.submit",
                            target_id=package.id,
                            reason=reason,
                            at=package.created_at,
                            details={"sha256": sha256, "bytes": counter.size},
                        )
                    )
                    # Задача ставится до коммита, как у генерации: воркер, не увидевший строку,
                    # повторит попытку (``TaskNotVisibleError``).
                    await self._queue.enqueue(
                        PACKAGE_TASK, {"package_id": str(package.id)}, task_id=str(package.id)
                    )
                    return PackageSubmitted(package.id, package.status).to_json()

                response, executed_now = await run_idempotent(
                    uow.idempotency,
                    actor_id,
                    idempotency_key,
                    request_fingerprint(
                        "package.submit", actor_id, {"sha256": sha256, "reason": reason}
                    ),
                    perform,
                    status_code=202,
                )
                await uow.commit()
                executed = executed_now
        finally:
            if not executed:
                # Повтор или ошибка: этот архив ни на что не ссылается.
                await delete_quietly(self._storage, StorageArea.SOURCES, [key])
        result = PackageSubmitted.from_json(response)
        log.info("package.submitted", package_id=str(result.package_id), bytes=counter.size)
        return result

    async def _guard(
        self, body: AsyncIterator[bytes], digest: Any, counter: _Counter
    ) -> AsyncIterator[bytes]:
        """Считать размер и хэш, проверить лимит и сигнатуру zip по ходу потока."""
        head = bytearray()
        async for chunk in body:
            if len(head) < len(_ZIP_MAGIC):
                head += chunk[: len(_ZIP_MAGIC) - len(head)]
            counter.size += len(chunk)
            if counter.size > self._limits.max_archive_bytes:
                raise PackageTooLargeError(
                    "архив больше лимита", details={"max_bytes": self._limits.max_archive_bytes}
                )
            if len(head) >= len(_ZIP_MAGIC) and bytes(head) != _ZIP_MAGIC:
                raise PackageRejectedError("это не zip-архив", code="package.not_zip")
            digest.update(chunk)
            yield chunk
        if bytes(head) != _ZIP_MAGIC:
            raise PackageRejectedError("это не zip-архив", code="package.not_zip")


class _Counter:
    size = 0


class ProcessPackage:
    """Разобрать пакет задачей: проверить архив и манифест, загрузить файлы по одному.

    Один плохой файл не останавливает остальные: ошибка попадает в статус пакета. Задача
    идемпотентна: ключи ``UploadSource`` выводятся из содержимого пакета, поэтому повтор после
    падения воркера не создаёт исходники заново. Содержимое файлов никогда не исполняется.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        storage: ObjectStorage,
        upload: UploadSource,
        *,
        limits: ArchiveLimits,
        max_upload_bytes: int,
        keep_errors: int,
        lease: timedelta,
    ) -> None:
        self._lease = lease
        self._uow = uow
        self._clock = clock
        self._storage = storage
        self._upload = upload
        self._limits = limits
        self._max_upload = max_upload_bytes
        self._keep_errors = keep_errors

    async def execute(self, package_id: UUID) -> None:
        """Обработать пакет.

        Raises:
            TaskNotVisibleError: пакета ещё не видно (задача поставлена до коммита) - повтор.
            DependencyUnavailableError: S3/БД/ffmpeg недоступны - задача вернётся в очередь.
        """
        async with self._uow() as uow:
            package = await uow.packages.get(package_id, for_update=True)
            if package is None:
                raise TaskNotVisibleError(f"пакет {package_id} не виден")
            if package.status.is_terminal or package.archive_key is None:
                return
            if not package.can_claim(self._clock.now(), self._lease):
                # Другой воркер обрабатывает пакет (повторная доставка задачи): вернуть в очередь
                # позже, а не терять задачу - вдруг тот воркер упадёт.
                raise TaskNotVisibleError(f"пакет {package_id} обрабатывается другим воркером")
            package.start(self._clock.now())
            archive_key = package.archive_key
            await uow.packages.save(package)
            await uow.commit()
        try:
            await self._run(package, archive_key)
        except DependencyUnavailableError:
            raise
        except PackageRejectedError as exc:
            await self._fail(package, exc.code, str(exc))
        except GenerationError as exc:
            if exc.retryable:
                raise DependencyUnavailableError("хранилище пакетов недоступно") from exc
            await self._fail(package, "package.archive_unavailable", "архив пакета недоступен")
        except Exception:
            # Неожиданное не должно оставлять пакет в `processing`, а архив - в S3 навсегда.
            log.exception("package.crashed", package_id=str(package.id))
            await self._fail(package, "package.internal_error", "внутренняя ошибка разбора пакета")
        await self._cleanup(package, archive_key)
        log.info(
            "package.processed",
            package_id=str(package.id),
            status=package.status.value,
            total=package.total,
            done=package.done,
            failed=package.failed,
        )

    async def _cleanup(self, package: ImportPackage, archive_key: str) -> None:
        """Убрать архив из S3, когда обработка закончена (успешно или отказом)."""
        if package.status.is_terminal:
            await delete_quietly(self._storage, StorageArea.SOURCES, [archive_key])

    async def _run(self, package: ImportPackage, archive_key: str) -> None:
        with tempfile.TemporaryDirectory(prefix="gv-package-") as tmp:
            path = Path(tmp) / "package.zip"
            await self._storage.download_to(
                StorageArea.SOURCES, archive_key, path, max_bytes=self._limits.max_archive_bytes
            )
            archive = await asyncio.to_thread(open_archive, path, self._limits)
            try:
                manifest = await self._manifest(archive)
                package.set_total(manifest.file_count)
                await self._save(package)
                for item in manifest.items:
                    await self._process_item(package, archive, item)
                package.finish(self._clock.now())
                await self._save(package)
            finally:
                archive.close()

    async def _manifest(self, archive: SafeArchive) -> PackageManifest:
        if MANIFEST_NAME not in archive.names:
            raise PackageRejectedError(
                "в архиве нет manifest.json", code="package.manifest_missing"
            )
        raw = await asyncio.to_thread(
            archive.read, MANIFEST_NAME, max_bytes=self._limits.max_manifest_bytes
        )
        return parse_manifest(raw, max_files=self._limits.max_entries)

    async def _process_item(
        self, package: ImportPackage, archive: SafeArchive, item: ManifestItem
    ) -> None:
        item_error = await self._item_error(item)
        seen_roles: dict[str, int] = {}
        for file in item.files:
            seen_roles[file.role] = seen_roles.get(file.role, 0) + 1
            position = file.position or seen_roles[file.role]
            if item_error is not None:
                self._failed(
                    package,
                    item,
                    path=file.path,
                    role=file.role,
                    code=item_error[0],
                    message=item_error[1],
                )
            else:
                await self._upload_file(package, archive, item, file, position)
            await self._save(package)

    async def _item_error(self, item: ManifestItem) -> tuple[str, str] | None:
        """Ошибка всей записи (единица не определена, неизвестна или другого вида)."""
        if item.media_item_id is None:
            return (
                "package.media_item_unresolved",
                "в записи нет media_item_id: ключ каталога сервис сам не разрешает",
            )
        async with self._uow() as uow:
            state = await uow.items.get(item.media_item_id)
        if state is None:
            return "media_item.not_found", "медиа-единица не найдена"
        if state.kind is not None and state.kind != item.kind:
            return "package.kind_mismatch", "вид записи не совпадает с видом единицы"
        return None

    async def _upload_file(
        self,
        package: ImportPackage,
        archive: SafeArchive,
        item: ManifestItem,
        file: Any,
        position: int,
    ) -> None:
        assert item.media_item_id is not None  # noqa: S101 — проверено в _item_error
        try:
            data = await asyncio.to_thread(archive.read, file.path, max_bytes=self._max_upload)
            result = await self._upload.execute(
                media_item_id=item.media_item_id,
                kind=file.kind,
                data=data,
                actor_id=package.actor_id,
                reason=package.reason,
                idempotency_key=self._upload_key(package, item, file.role, file.path, position),
                position=position,
                provenance=SourceProvenance(
                    origin=item.origin,
                    source_url=item.source_url,
                    license_note=item.license_note,
                ),
            )
        except DependencyUnavailableError:
            raise
        except AppError as exc:
            self._failed(
                package, item, path=file.path, role=file.role, code=exc.code, message=str(exc)
            )
        except Exception:
            # Один файл не должен ронять пакет; причину видно в логе, клиенту - без деталей.
            log.exception("package.file_failed", package_id=str(package.id), key=item.key)
            self._failed(
                package,
                item,
                path=file.path,
                role=file.role,
                code="package.internal_error",
                message="ошибка обработки файла",
            )
        else:
            package.record_done(
                source_missing=result.source_missing, license_missing=result.license_missing
            )

    def _failed(
        self,
        package: ImportPackage,
        item: ManifestItem,
        *,
        path: str,
        role: str,
        code: str,
        message: str,
    ) -> None:
        package.record_failed(
            item_key=item.key,
            path=path,
            role=role,
            code=code,
            message=message,
            keep_errors=self._keep_errors,
        )

    @staticmethod
    def _upload_key(
        package: ImportPackage, item: ManifestItem, role: str, path: str, position: int
    ) -> str:
        """Детерминированный ключ идемпотентности файла: тот же пакет и причина - тот же ключ."""
        raw = "\0".join(
            [package.archive_sha256, package.reason, item.key, role, path, str(position)]
        )
        return "pkg-" + hashlib.sha256(raw.encode()).hexdigest()[:48]

    async def _save(self, package: ImportPackage) -> None:
        package.touch(self._clock.now())
        async with self._uow() as uow:
            await uow.packages.save(package)
            await uow.commit()

    async def _fail(self, package: ImportPackage, code: str, message: str) -> None:
        package.fail(self._clock.now(), code=code, message=message)
        await self._save(package)
        log.warning("package.rejected", package_id=str(package.id), code=code)


class SweepOrphanPackageArchives:
    """Чистка осиротевших архивов ``k/*``: загрузили в S3, а строка пакета не появилась
    (сбой между записью объекта и коммитом).

    Удаляются объекты старше ``ttl`` без ссылки из незавершённого пакета; свежие не трогаем
    (пакет может быть в процессе записи). Архив завершённого пакета удаляет сам воркер.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        storage: ObjectStorage,
        clock: Clock,
        *,
        ttl: timedelta,
        page_size: int = 500,
    ) -> None:
        self._uow = uow
        self._storage = storage
        self._clock = clock
        self._ttl = ttl
        self._page_size = page_size

    async def sweep(self) -> int:
        """Пройти префикс ``k/`` страницами; вернуть число удалённых объектов."""
        cutoff = self._clock.now() - self._ttl
        deleted = 0
        token: str | None = None
        while True:
            page = await self._storage.list_objects(
                StorageArea.SOURCES, PACKAGE_PREFIX, limit=self._page_size, token=token
            )
            old = [o.key for o in page.items if o.last_modified < cutoff]
            if old:
                async with self._uow() as uow:
                    referenced = await uow.packages.active_archive_keys(old)
                orphans = [k for k in old if k not in referenced]
                if orphans:
                    await self._storage.delete(StorageArea.SOURCES, orphans)
                    deleted += len(orphans)
            token = page.next_token
            if token is None:
                break
        if deleted:
            log.info("package.orphans_deleted", count=deleted)
        return deleted
