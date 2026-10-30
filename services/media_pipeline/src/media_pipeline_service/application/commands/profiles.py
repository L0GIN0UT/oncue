"""Динамические профили (ADR 0025): заведение по ``game.ClueAssetsRequested`` и досоздание
наборов порциями (периодическая задача ``media.profiles.backfill``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_OID, UUID, uuid5

import structlog

from gv_core.clock import Clock
from gv_core.errors import InvalidInputError
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent
from media_pipeline_service.application.commands.catalog import PlanAssetSets
from media_pipeline_service.application.ports import (
    AdminAction,
    ObjectStorage,
    StorageArea,
    UnitOfWorkFactory,
)
from media_pipeline_service.application.profiles import ProfileDirectory
from media_pipeline_service.application.sources import delete_quietly
from media_pipeline_service.domain.dynamic_profiles import (
    LIMIT_REACHED,
    DynamicProfile,
    DynamicProfileStatus,
    dynamic_profile_key,
)
from media_pipeline_service.domain.errors import InvalidProfileError
from media_pipeline_service.domain.params import canonical_json, params_hash

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ClueAssetsRequest:
    """Запрос ассетов из ``game.ClueAssetsRequested`` v1."""

    request_id: UUID
    mode_key: str
    draft_revision: int
    domain: str
    clue_type: str
    params: dict[str, Any]
    params_hash: str
    actor_id: UUID
    requested_at: datetime


class ApplyClueAssetsRequested:
    """Завести динамический профиль под запрошенные параметры загадки.

    Идемпотентность: повтор события отсекает inbox; другой запрос с теми же
    ``(clue_type, params_hash, domain)`` ничего не создаёт; параметры, уже покрытые
    статическим профилем, профиля не заводят. Параметры проверяет модель генератора (та же,
    что у статических профилей, и повторяет ``contracts/clues``); неверные параметры,
    несовпавший хэш, превышение лимита и слишком большие параметры дают профиль
    ``rejected`` с кодом — его видно в ``GET /admin/v1/profiles``. Лимит проверяется под
    advisory lock: параллельные реплики consumer не превысят его.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        profiles: ProfileDirectory,
        *,
        max_profiles: int,
        max_params_bytes: int,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._profiles = profiles
        self._max_profiles = max_profiles
        self._max_params_bytes = max_params_bytes

    async def execute(self, request: ClueAssetsRequest) -> DynamicProfile | None:
        """Применить запрос. ``None`` — параметры покрыты статическим профилем."""
        static = self._profiles.static.find(request.clue_type, request.params_hash, request.domain)
        if static is not None:
            log.info(
                "dynamic_profile.covered_by_static",
                clue_type=request.clue_type,
                profile_key=static.profile_key,
            )
            return None
        now = self._clock.now()
        async with self._uow() as uow:
            await uow.profiles.lock()
            existing = await uow.profiles.get(
                request.clue_type, request.params_hash, request.domain, for_update=True
            )
            if existing is not None and (
                existing.is_usable or existing.error_code != LIMIT_REACHED
            ):
                log.info(
                    "dynamic_profile.duplicate",
                    clue_type=request.clue_type,
                    status=existing.status.value,
                )
                await uow.commit()
                return existing
            error = self._check(request)
            if error is None and await uow.profiles.count_usable() >= self._max_profiles:
                error = LIMIT_REACHED
            if existing is not None:
                profile = existing
                if error is None:
                    profile.revive(
                        request_id=request.request_id,
                        actor_id=request.actor_id,
                        mode_key=request.mode_key,
                        now=now,
                    )
                else:
                    profile.reject(error, request_id=request.request_id, now=now)
                await uow.profiles.save(profile)
            else:
                profile = self._new(request, error, now)
                await uow.profiles.add(profile)
            await uow.commit()
        log.info(
            "dynamic_profile.requested",
            clue_type=profile.clue_type,
            domain=profile.domain,
            status=profile.status.value,
            error_code=profile.error_code,
            mode_key=profile.mode_key,
        )
        return profile

    def _check(self, request: ClueAssetsRequest) -> str | None:
        """Код отказа или ``None``."""
        try:
            size = len(canonical_json(request.params).encode())
            if size > self._max_params_bytes:
                return "profile.params_too_large"
            if params_hash(request.params) != request.params_hash:
                return "profile.hash_mismatch"
        except (TypeError, ValueError):
            return "profile.invalid"
        try:
            self._profiles.registry.build_dynamic(
                clue_type=request.clue_type,
                profile_key=dynamic_profile_key(request.params_hash),
                domain=request.domain,
                params=request.params,
            )
        except InvalidProfileError as exc:
            return exc.code
        return None

    def _new(self, request: ClueAssetsRequest, error: str | None, now: datetime) -> DynamicProfile:
        too_large = error == "profile.params_too_large"
        return DynamicProfile(
            uuid7(),
            clue_type=request.clue_type,
            domain=request.domain,
            # Слишком большие параметры не храним: отказ виден по коду и хэшу.
            params={} if too_large else request.params,
            params_hash=request.params_hash,
            status=(
                DynamicProfileStatus.BACKFILLING if error is None else DynamicProfileStatus.REJECTED
            ),
            error_code=error,
            request_id=request.request_id,
            mode_key=request.mode_key,
            actor_id=request.actor_id,
            backfill_cursor=None,
            created_at=now,
            updated_at=now,
        )


class BackfillDynamicProfiles:
    """Досоздать наборы динамических профилей для уже известных единиц, порциями.

    За запуск обрабатывается не больше ``batch`` единиц (по всем профилям вместе): так
    нагрузка на ffmpeg и S3 растёт постепенно. Курсор — ``media_item_id`` последней
    обработанной единицы; порция и курсор фиксируются в одной транзакции, поэтому повтор
    после сбоя продолжает с того же места (наборы уникальны — дублей нет). Новые единицы
    планируются событиями catalog по общему каталогу профилей. Наборы сборников (ADR 0029)
    досоздаются вместе с каталожными: по каждому владельцу своих исходников единицы.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        profiles: ProfileDirectory,
        plan: PlanAssetSets,
        storage: ObjectStorage,
        *,
        batch: int,
    ) -> None:
        if batch < 1:
            raise ValueError("batch должен быть положительным")
        self._uow = uow
        self._clock = clock
        self._profiles = profiles
        self._plan = plan
        self._storage = storage
        self._batch = batch

    async def execute(self) -> int:
        """Обработать порцию; вернуть число единиц, по которым шло планирование."""
        async with self._uow() as uow:
            pending = await uow.profiles.list_backfilling(limit=self._batch)
        budget = self._batch
        processed = 0
        for dynamic in pending:
            if budget <= 0:
                break
            done = await self._step(dynamic, budget)
            budget -= done
            processed += done
        if processed:
            log.info("dynamic_profiles.backfilled", items=processed)
        return processed

    async def _step(self, dynamic: DynamicProfile, budget: int) -> int:
        profile = self._profiles.profile_of(dynamic)
        stale: list[str] = []
        async with self._uow() as uow:
            fresh = await uow.profiles.get(
                dynamic.clue_type, dynamic.params_hash, dynamic.domain, for_update=True
            )
            if fresh is None or fresh.status is not DynamicProfileStatus.BACKFILLING:
                await uow.commit()
                return 0
            now = self._clock.now()
            if profile is None:
                fresh.reject("profile.invalid", request_id=fresh.request_id, now=now)
                await uow.profiles.save(fresh)
                await uow.commit()
                return 0
            ids = await uow.items.list_active_ids(
                fresh.domain, after=fresh.backfill_cursor, limit=budget
            )
            for media_item_id in ids:
                planned = await self._plan.plan_in(uow, media_item_id, only=profile)
                stale.extend(planned.stale_keys)
                # Свои загадки сборников (ADR 0029): тот же профиль по исходникам владельца.
                for scope in await uow.sources.scopes_for_item(media_item_id):
                    planned = await self._plan.plan_in(
                        uow, media_item_id, only=profile, scope=scope
                    )
                    stale.extend(planned.stale_keys)
            fresh.advance(ids[-1] if ids else None, done=len(ids) < budget, now=now)
            await uow.profiles.save(fresh)
            await uow.commit()
        await delete_quietly(self._storage, StorageArea.ASSETS, stale)
        return len(ids)


@dataclass(frozen=True, slots=True)
class ReplanBatch:
    """Итог порции пересоздания наборов.

    Attributes:
        processed: Сколько единиц просмотрено.
        scheduled: Сколько наборов поставлено в генерацию (новых и перепланированных).
        next_after: Курсор следующей порции; ``None`` — домен пройден до конца.
    """

    processed: int
    scheduled: int
    next_after: UUID | None

    def to_json(self) -> dict[str, Any]:
        """Для хранения ответа идемпотентной мутации."""
        return {
            "processed": self.processed,
            "scheduled": self.scheduled,
            "next_after": str(self.next_after) if self.next_after else None,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ReplanBatch:
        """Обратное преобразование ``to_json``."""
        after = data["next_after"]
        return cls(data["processed"], data["scheduled"], UUID(after) if after else None)


class ReplanStaticProfiles:
    """Досоздать наборы СТАТИЧЕСКИХ профилей типа загадки для уже известных единиц, порциями.

    Нужна после правки ``config/asset_profiles.yaml``: новые параметры меняют ``params_hash``,
    а событий catalog по старым единицам не будет, поэтому наборов под новый хэш у них нет.
    Работает тем же ``PlanAssetSets.plan_in(only=…)``, что и досоздание динамических профилей
    (``BackfillDynamicProfiles``): создаёт только недостающие наборы, существующие не трогает
    (старые наборы с прежним хэшем остаются — по ним доигрываются начатые сессии и наборы дня).
    Повтор безопасен. Курсор — ``media_item_id`` последней просмотренной единицы домена.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        profiles: ProfileDirectory,
        plan: PlanAssetSets,
        storage: ObjectStorage,
        *,
        max_limit: int,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._profiles = profiles
        self._plan = plan
        self._storage = storage
        self._max_limit = max_limit

    async def execute(
        self,
        *,
        clue_type: str,
        domain: str,
        after: UUID | None,
        limit: int,
        actor_id: UUID,
        reason: str,
        idempotency_key: str,
    ) -> ReplanBatch:
        """Обработать порцию единиц домена (идемпотентно по ``(actor_id, idempotency_key)``).

        Raises:
            InvalidInputError: ``limit`` вне ``1..max_limit`` или нет статического профиля
                ``clue_type`` для домена (``profile.not_found``).
            ConflictError: ``idempotency.key_reused`` / ``idempotency.in_progress``.
        """
        if not 1 <= limit <= self._max_limit:
            raise InvalidInputError("limit вне допустимого диапазона", code="replan.bad_limit")
        selected = [p for p in self._profiles.static.for_domain(domain) if p.clue_type == clue_type]
        if not selected:
            raise InvalidInputError("нет статического профиля", code="profile.not_found")
        fingerprint = request_fingerprint(
            "asset_sets.replan",
            clue_type,
            {"domain": domain, "after": str(after) if after else None, "limit": limit,
             "reason": reason},
        )  # fmt: skip
        stale: list[str] = []
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                ids = await uow.items.list_active_ids(domain, after=after, limit=limit)
                scheduled = 0
                for media_item_id in ids:
                    for profile in selected:
                        planned = await self._plan.plan_in(uow, media_item_id, only=profile)
                        scheduled += len(planned.asset_set_ids)
                        stale.extend(planned.stale_keys)
                now = self._clock.now()
                await uow.audit.record(
                    AdminAction(
                        actor_id=actor_id,
                        action="asset_sets.replan",
                        target_id=uuid5(NAMESPACE_OID, f"asset_sets.replan:{clue_type}:{domain}"),
                        reason=reason,
                        at=now,
                        details={
                            "clue_type": clue_type,
                            "domain": domain,
                            "items": len(ids),
                            "scheduled": scheduled,
                        },
                    )
                )
                done = len(ids) < limit
                return ReplanBatch(len(ids), scheduled, None if done else ids[-1]).to_json()

            response, executed = await run_idempotent(
                uow.idempotency, actor_id, idempotency_key, fingerprint, perform, status_code=200
            )
            await uow.commit()
        batch = ReplanBatch.from_json(response)
        if executed:
            await delete_quietly(self._storage, StorageArea.ASSETS, stale)
            log.info(
                "asset_sets.replanned",
                clue_type=clue_type,
                domain=domain,
                items=batch.processed,
                scheduled=batch.scheduled,
            )
        return batch
