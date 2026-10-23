"""UGC-изображения (ADR 0048): приём от доверенного сервиса и решения модератора."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import ConflictError
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent
from media_pipeline_service.application.ports import (
    AdminAction,
    ObjectStorage,
    StorageArea,
    TokenSource,
    UgcImageEncoder,
    UnitOfWorkFactory,
    UrlSigner,
)
from media_pipeline_service.application.queries.ugc import ListUgcQueue
from media_pipeline_service.application.sources import delete_quietly
from media_pipeline_service.application.ugc import UgcImageView, UgcPreview
from media_pipeline_service.domain.errors import (
    UgcClientForbiddenError,
    UgcNotFoundError,
    UgcTooLargeError,
)
from media_pipeline_service.domain.ugc import (
    PENDING_PREFIX,
    UgcCatalog,
    UgcImage,
    UgcStatus,
    pending_key,
    public_key,
)

log = structlog.get_logger(__name__)


class UgcObjectPurger:
    """Гарантированное удаление объектов UGC (снятие, отклонение, замена, отмена заявки).

    Ключи пишутся в ``ugc_object_purge`` в той же транзакции, что и решение; после коммита
    объекты удаляются сразу (быстрый путь), а что не удалось - удаляет периодическая задача
    ``media.ugc.purge`` до успеха. Снятое изображение не остаётся доступным по ``u/``
    дольше одного интервала задачи (плюс кэш gateway).
    """

    def __init__(self, uow: UnitOfWorkFactory, storage: ObjectStorage, *, batch: int = 500) -> None:
        self._uow = uow
        self._storage = storage
        self._batch = batch

    async def delete_now(self, keys: Sequence[str]) -> None:
        """Быстрый путь после коммита; любой сбой оставляет ключи в очереди для задачи."""
        if not keys:
            return
        try:
            await self._storage.delete(StorageArea.ASSETS, keys)
            async with self._uow() as uow:
                await uow.ugc.forget_purge(keys)
                await uow.commit()
        except Exception:
            log.warning("ugc.purge_deferred", count=len(keys), exc_info=True)

    async def sweep(self) -> int:
        """Удалить накопленное порциями; остановиться на первом сбое хранилища.

        Returns:
            Сколько ключей удалено и убрано из очереди.
        """
        total = 0
        while True:
            async with self._uow() as uow:
                keys = await uow.ugc.claim_purge(self._batch)
                if not keys:
                    return total
                try:
                    await self._storage.delete(StorageArea.ASSETS, keys)
                except Exception:
                    log.warning("ugc.purge_failed", count=len(keys), exc_info=True)
                    return total  # строки остаются: повтор на следующем запуске
                await uow.ugc.forget_purge(keys)
                await uow.commit()
            total += len(keys)


class SweepOrphanUgcObjects:
    """Чистка осиротевших ``p/``-объектов: загрузили в S3, а строка заявки не появилась
    (сбой между записью объекта и коммитом).

    Удаляются объекты старше ``ttl`` без ``pending``-строки; свежие не трогаем (заявка может
    быть в процессе записи), объекты с ``pending``-строкой - никогда.
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
        """Пройти префикс ``p/`` страницами; вернуть число удалённых объектов."""
        cutoff = self._clock.now() - self._ttl
        deleted = 0
        token: str | None = None
        while True:
            page = await self._storage.list_objects(
                StorageArea.ASSETS, PENDING_PREFIX, limit=self._page_size, token=token
            )
            old = [o.key for o in page.items if o.last_modified < cutoff]
            if old:
                async with self._uow() as uow:
                    referenced = await uow.ugc.pending_keys(old)
                orphans = [k for k in old if k not in referenced]
                if orphans:
                    await self._storage.delete(StorageArea.ASSETS, orphans)
                    deleted += len(orphans)
            token = page.next_token
            if token is None:
                break
        if deleted:
            log.info("ugc.orphans_deleted", count=deleted)
        return deleted


class SubmitUgcImage:
    """Принять изображение от сервиса-клиента (``POST /internal/v1/ugc-images``).

    Тип и размеры определяются по содержимому, результат пересобирается из пикселей;
    ключ объекта - случайный (``p/<128 бит hex>.webp``), имя и ``Content-Type`` клиента не
    используются. Новая заявка владельца на то же назначение тихо отменяет прежнюю
    ``pending`` (событие не публикуется).
    """

    def __init__(  # noqa: PLR0917 - зависимости use-case
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        encoder: UgcImageEncoder,
        storage: ObjectStorage,
        tokens: TokenSource,
        catalog: UgcCatalog,
        purger: UgcObjectPurger,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._encoder = encoder
        self._storage = storage
        self._tokens = tokens
        self._catalog = catalog
        self._purger = purger

    async def execute(
        self,
        *,
        client_id: str,
        owner_user_id: UUID,
        purpose: str,
        data: bytes,
        idempotency_key: str,
    ) -> UgcImageView:
        """Принять файл (идемпотентно по ``(owner_user_id, Idempotency-Key)``).

        Raises:
            UgcPurposeUnknownError: назначения нет в конфиге.
            UgcClientForbiddenError: клиенту это назначение запрещено.
            UgcTooLargeError: файл больше лимита назначения.
            InvalidUgcImageError: файл не прошёл проверку.
            ConflictError: ключ уже использован с другим запросом.
        """
        spec = self._catalog.purpose(purpose)
        if client_id not in spec.allowed_clients:
            raise UgcClientForbiddenError("сервису это назначение недоступно")
        if len(data) > spec.max_bytes:
            raise UgcTooLargeError("файл больше лимита", details={"max_bytes": spec.max_bytes})
        fingerprint = request_fingerprint(
            "ugc.submit",
            owner_user_id,
            {"purpose": purpose, "sha256": hashlib.sha256(data).hexdigest()},
        )
        async with self._uow() as uow:
            # Повтор отвечаем до перекодирования файла.
            existing = await uow.idempotency.find(owner_user_id, idempotency_key)
        if existing is not None:
            if existing.request_hash != fingerprint:
                raise ConflictError(
                    "Idempotency-Key уже использован с другим запросом",
                    code="idempotency.key_reused",
                )
            return UgcImageView.from_json(existing.response)

        encoded = await self._encoder.encode(data, spec)
        key = pending_key(self._tokens.token_hex())
        await self._storage.put(StorageArea.ASSETS, key, encoded.data, "image/webp")
        superseded: list[str] = []
        executed = False
        committed = False
        try:
            async with self._uow() as uow:

                async def perform() -> dict[str, Any]:
                    await uow.ugc.lock_owner(owner_user_id, purpose)
                    now = self._clock.now()
                    for old in await uow.ugc.list_pending_for_owner(owner_user_id, purpose):
                        superseded.append(old.supersede(now))
                        await uow.ugc.save(old)
                    await uow.ugc.enqueue_purge(superseded, now)
                    image = UgcImage.submit(
                        uuid7(),
                        owner_user_id=owner_user_id,
                        purpose=purpose,
                        pending_key=key,
                        width=encoded.width,
                        height=encoded.height,
                        size_bytes=len(encoded.data),
                        sha256=encoded.sha256,
                        now=now,
                    )
                    await uow.ugc.add(image)
                    return UgcImageView.of(image).to_json()

                response, executed = await run_idempotent(
                    uow.idempotency,
                    owner_user_id,
                    idempotency_key,
                    fingerprint,
                    perform,
                    status_code=202,
                )
                await uow.commit()
                committed = True
        finally:
            if not (executed and committed):
                # Заявка не создана (ошибка или параллельный повтор успел раньше).
                await delete_quietly(self._storage, StorageArea.ASSETS, [key])
        await self._purger.delete_now(superseded)
        view = UgcImageView.from_json(response)
        log.info("ugc.submitted", asset_id=str(view.asset_id), purpose=purpose, client=client_id)
        return view


class ModerateUgcImage:
    """Решения модератора: одобрить, отклонить или снять одобренное.

    Строка ассета блокируется ``FOR UPDATE``: из двух конкурирующих решений побеждает
    одно, второе видит новый статус (409). Статус, аудит и ``AssetModerated`` пишутся
    одной транзакцией; объекты S3 меняются так, чтобы ошибка не оставила доступным то, что
    не одобрено: копия ``u/`` создаётся до коммита (и удаляется при неудаче), исходный
    ``p/`` и снятый ``u/`` удаляются после коммита.
    """

    def __init__(  # noqa: PLR0917 - зависимости use-case
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        storage: ObjectStorage,
        tokens: TokenSource,
        catalog: UgcCatalog,
        purger: UgcObjectPurger,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._storage = storage
        self._tokens = tokens
        self._catalog = catalog
        self._purger = purger

    async def approve(
        self, asset_id: UUID, *, moderator_id: UUID, reason: str, idempotency_key: str
    ) -> UgcImageView:
        """``pending → approved`` (409 ``ugc.not_pending`` из других статусов)."""
        fingerprint = request_fingerprint("ugc.approve", asset_id, {"reason": reason})
        copied: list[str] = []
        stale: list[str] = []
        committed = False
        try:
            async with self._uow() as uow:

                async def perform() -> dict[str, Any]:
                    # Порядок блокировок как у приёма: advisory-lock владельца -> строки.
                    probe = await uow.ugc.get(asset_id)
                    if probe is None:
                        raise UgcNotFoundError("изображение не найдено")
                    await uow.ugc.lock_owner(probe.owner_user_id, probe.purpose)
                    image = await uow.ugc.get(asset_id, for_update=True)
                    if image is None:
                        raise UgcNotFoundError("изображение не найдено")
                    new_key = public_key(self._tokens.token_hex())
                    # Статус проверяет агрегат; копия - только для ``pending``.
                    if image.status is UgcStatus.PENDING and image.pending_key:
                        await self._storage.copy(StorageArea.ASSETS, image.pending_key, new_key)
                        copied.append(new_key)
                    now = self._clock.now()
                    stale.append(
                        image.approve(moderator_id=moderator_id, new_public_key=new_key, now=now)
                    )
                    await uow.ugc.save(image)
                    uow.track(image)
                    # Новый аватар заменяет прежние одобренные владельца: тихо (без события),
                    # их публичные объекты уходят в очередь удаления.
                    for old in await uow.ugc.list_approved_for_owner(
                        image.owner_user_id, image.purpose, exclude=image.id
                    ):
                        stale.append(old.replace(now))
                        await uow.ugc.save(old)
                    await uow.ugc.enqueue_purge(stale, now)
                    await uow.audit.record(
                        AdminAction(
                            actor_id=moderator_id,
                            action="ugc.approve",
                            target_id=asset_id,
                            reason=reason,
                            at=now,
                            details={"purpose": image.purpose, "version": image.version},
                        )
                    )
                    return UgcImageView.of(image).to_json()

                response, executed = await run_idempotent(
                    uow.idempotency, moderator_id, idempotency_key, fingerprint, perform
                )
                await uow.commit()
                committed = True
        finally:
            if not committed and copied:
                await delete_quietly(self._storage, StorageArea.ASSETS, copied)
        if executed:
            await self._purger.delete_now(stale)
            log.info("ugc.approved", asset_id=str(asset_id))
        return UgcImageView.from_json(response)

    async def reject(
        self,
        asset_id: UUID,
        *,
        moderator_id: UUID,
        reason_code: str,
        reason: str,
        idempotency_key: str,
    ) -> UgcImageView:
        """``pending|approved → rejected`` с кодом причины из конфига."""
        self._catalog.check_reason(reason_code)
        fingerprint = request_fingerprint(
            "ugc.reject", asset_id, {"reason": reason, "reason_code": reason_code}
        )
        stale: list[str] = []
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                image = await uow.ugc.get(asset_id, for_update=True)
                if image is None:
                    raise UgcNotFoundError("изображение не найдено")
                previous = image.status
                now = self._clock.now()
                stale.append(
                    image.reject(moderator_id=moderator_id, reason_code=reason_code, now=now)
                )
                await uow.ugc.save(image)
                uow.track(image)
                await uow.ugc.enqueue_purge(stale, now)
                await uow.audit.record(
                    AdminAction(
                        actor_id=moderator_id,
                        action="ugc.reject",
                        target_id=asset_id,
                        reason=reason,
                        at=now,
                        details={
                            "purpose": image.purpose,
                            "reason_code": reason_code,
                            "from": previous.value,
                            "version": image.version,
                        },
                    )
                )
                return UgcImageView.of(image).to_json()

            response, executed = await run_idempotent(
                uow.idempotency, moderator_id, idempotency_key, fingerprint, perform
            )
            await uow.commit()
        if executed:
            await self._purger.delete_now(stale)
            log.info("ugc.rejected", asset_id=str(asset_id), reason_code=reason_code)
        return UgcImageView.from_json(response)


class PreviewUgcImage:
    """Короткая ссылка модератору: ``pending`` - подписанная ``/media/p/…``, ``approved`` -
    публичный адрес. Выдача пишется в аудит; у отклонённого объекта нет (404)."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        signer: UrlSigner,
        *,
        ttl_s: int,
        public_base_url: str,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._signer = signer
        self._ttl = timedelta(seconds=ttl_s)
        self._public_base = public_base_url

    async def execute(self, asset_id: UUID, *, moderator_id: UUID) -> UgcPreview:
        """Выдать ссылку.

        Raises:
            UgcNotFoundError: ассета нет или он отклонён.
        """
        async with self._uow() as uow:
            image = await uow.ugc.get(asset_id)
            if image is None:
                raise UgcNotFoundError("изображение не найдено")
            now = self._clock.now()
            expires_at = now + self._ttl
            if image.status is UgcStatus.PENDING and image.pending_key:
                url = self._signer.sign(image.pending_key, expires_at)
            elif image.status is UgcStatus.APPROVED and image.public_key:
                url = f"{self._public_base}{image.public_key}"
            else:
                raise UgcNotFoundError("у изображения нет объекта")
            await uow.audit.record(
                AdminAction(
                    actor_id=moderator_id,
                    action="ugc.preview",
                    target_id=asset_id,
                    reason="просмотр модератором",
                    at=now,
                    details={"status": image.status.value},
                )
            )
            await uow.commit()
        return UgcPreview(url=url, expires_at=expires_at)


@dataclass(frozen=True, slots=True)
class UgcUseCases:
    """Набор use-cases UGC для API (сборка - в контейнере)."""

    catalog: UgcCatalog
    submit: SubmitUgcImage
    moderate: ModerateUgcImage
    preview: PreviewUgcImage
    queue: ListUgcQueue
    purger: UgcObjectPurger
    orphans: SweepOrphanUgcObjects
