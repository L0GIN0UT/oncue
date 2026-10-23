"""Своя аватарка: право, заявка в media-pipeline и применение решений модератора (ADR 0048).

* право - предмет из ``shop.custom_avatar_item_key`` в инвентаре игрока;
* файл проверяет и хранит media-pipeline (один вызов, не цепочка); до решения модератора виден
  прежний аватар, новая заявка отменяет прежнюю ``pending``;
* ``AssetModerated`` применяется идемпотентно по ``version`` на ``asset_id``: событие с версией
  не больше применённой пропускается, порядок доставки не важен.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.ids import uuid7
from progression_service.application.inventory import shop_config
from progression_service.application.ports import ProgressionUnitOfWork, UnitOfWorkFactory
from progression_service.application.ports.avatar import MediaUgcClient
from progression_service.application.ports.shop import AttemptLimiter
from progression_service.domain.avatar import (
    PURPOSE,
    AvatarNotEntitledError,
    AvatarRateLimitedError,
    AvatarRequest,
    CurrentAvatar,
    Moderation,
    RequestStatus,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AvatarState:
    """Состояние своей аватарки игрока."""

    entitled: bool
    current: CurrentAvatar | None
    request: AvatarRequest | None


async def _entitled(uow: ProgressionUnitOfWork, user_id: UUID, now: datetime) -> bool:
    cfg = await shop_config(uow)
    key = None if cfg is None else cfg.custom_avatar_item_key
    if key is None:
        return False
    item = await uow.items.get_by_key(key)
    if item is None:
        return False
    return await uow.inventory.get(user_id, item.id, now) is not None


class AvatarService:
    """Чтение состояния, заявка на аватарку и применение решений модератора."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        media: MediaUgcClient,
        *,
        limiter: AttemptLimiter | None = None,
        submit_limit: int = 0,
        submit_window_s: int = 1,
    ) -> None:
        self._limiter = limiter
        self._submit_limit = submit_limit
        self._submit_window_s = submit_window_s
        self._uow = uow
        self._clock = clock
        self._media = media

    async def mine(self, user_id: UUID) -> AvatarState:
        """Право, действующий аватар и последняя заявка (без обращения к media-pipeline)."""
        async with self._uow() as uow:
            return AvatarState(
                entitled=await _entitled(uow, user_id, self._clock.now()),
                current=await uow.avatars.current(user_id),
                request=await uow.avatars.latest_request(user_id),
            )

    async def submit(self, user_id: UUID, data: bytes, idempotency_key: str) -> AvatarRequest:
        """Подать заявку: право -> файл в media-pipeline -> запись заявки.

        Повтор с тем же ``Idempotency-Key`` получает тот же ассет от media-pipeline и ту же
        заявку; прежние ``pending`` заявки игрока закрываются как ``superseded``.

        Raises:
            AvatarNotEntitledError: товар не куплен (403).
            AvatarInvalidImageError: файл отклонён (422).
            AvatarTooLargeError: файл больше лимита (413).
            AvatarUnavailableError: media-pipeline недоступен (503).
            AvatarRateLimitedError: больше заявок за окно, чем разрешено (429).
            DependencyUnavailableError: лимитер недоступен (503, fail-closed).
        """
        async with self._uow() as uow:
            if not await _entitled(uow, user_id, self._clock.now()):
                raise AvatarNotEntitledError
        if self._limiter is not None:  # до media: декодирование картинки дорого
            window = await self._limiter.hit(f"avatar:{user_id}", self._submit_window_s)
            if window.count > self._submit_limit:
                raise AvatarRateLimitedError(window.resets_in_s)
        upload = await self._media.submit(
            owner_user_id=user_id, purpose=PURPOSE, data=data, idempotency_key=idempotency_key
        )
        async with self._uow() as uow:
            # Инвариант схемы: одна pending на игрока (частичный UNIQUE). Прежняя закрывается
            # ДО вставки новой, заявки игрока сериализует блокировка.
            await uow.avatars.lock_user(user_id)
            created = await uow.avatars.request_by_asset(upload.asset_id) is None
            if created:
                await uow.avatars.supersede_pending(user_id, upload.asset_id, self._clock.now())
                created = await uow.avatars.add_request(
                    user_id, upload.asset_id, upload.submitted_at, uuid7()
                )
            request = await uow.avatars.request_by_asset(upload.asset_id)
            await uow.commit()
        if request is None or request.user_id != user_id:  # чужой ассет не отдаём
            raise AvatarNotEntitledError
        log.info("progression.avatar_requested", repeated=not created)
        return request

    async def apply(self, decision: Moderation) -> bool:
        """Применить ``AssetModerated`` (в транзакции consumer).

        Returns:
            ``False`` - событие устарело или повторное (эффекта нет).
        """
        async with self._uow() as uow:
            if not await uow.avatars.apply_decision(decision):
                await uow.commit()
                return False
            if decision.status is RequestStatus.APPROVED:
                await uow.avatars.set_current(decision)
            else:
                await uow.avatars.clear_current(decision.owner_user_id, decision.asset_id)
            await uow.commit()
        log.info(
            "progression.avatar_moderated", status=decision.status.value, version=decision.version
        )
        return True
