"""Профили ассетов и готовность их наборов (``GET /admin/v1/profiles``, ADR 0025)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from media_pipeline_service.application.ports import UnitOfWorkFactory
from media_pipeline_service.application.profiles import ProfileDirectory
from media_pipeline_service.domain.asset_set import AssetSetStatus
from media_pipeline_service.domain.profiles import ProfileOrigin

# Какое состояние набора в какой счётчик: выключенные (блокировка единицы) не считаются.
_BUCKET = {
    AssetSetStatus.READY.value: "ready",
    AssetSetStatus.PENDING.value: "pending",
    AssetSetStatus.PROCESSING.value: "pending",
    AssetSetStatus.FAILED.value: "failed",
}


@dataclass(frozen=True, slots=True)
class ProfileView:
    """Профиль для админки.

    Attributes:
        profile_key: Ключ профиля; ``None`` у динамического (в наборах — ``dyn_<хэш>``).
        status: ``active`` | ``backfilling`` | ``rejected`` (статический — всегда ``active``).
        error_code: Почему отклонён динамический профиль.
    """

    clue_type: str
    profile_key: str | None
    params_hash: str
    origin: ProfileOrigin
    status: str
    domains: tuple[str, ...]
    error_code: str | None
    ready: int
    pending: int
    failed: int


class ListProfiles:
    """Статические и динамические профили со счётчиками наборов."""

    def __init__(self, uow: UnitOfWorkFactory, profiles: ProfileDirectory) -> None:
        self._uow = uow
        self._profiles = profiles

    async def execute(
        self, clue_type: str | None = None, params_hash: str | None = None
    ) -> list[ProfileView]:
        """Профили (фильтры — по типу загадки и хэшу параметров)."""
        async with self._uow() as uow:
            dynamic = await uow.profiles.list_all(clue_type, params_hash)
            counts = await uow.asset_sets.count_by_profile(clue_type, params_hash)
        by_profile: dict[tuple[str, str, str], Counter[str]] = {}
        for row in counts:
            bucket = _BUCKET.get(row.status)
            if bucket is not None:
                key = (row.clue_type, row.profile_key, row.params_hash)
                by_profile.setdefault(key, Counter())[bucket] += row.count

        def sets(clue: str, key: str, hash_: str) -> Counter[str]:
            return by_profile.get((clue, key, hash_), Counter())

        views: list[ProfileView] = []
        for profile in self._profiles.static:
            if clue_type not in (None, profile.clue_type) or params_hash not in (
                None,
                profile.params_hash,
            ):
                continue
            c = sets(profile.clue_type, profile.profile_key, profile.params_hash)
            views.append(
                ProfileView(
                    clue_type=profile.clue_type,
                    profile_key=profile.profile_key,
                    params_hash=profile.params_hash,
                    origin=ProfileOrigin.STATIC,
                    status="active",
                    domains=tuple(sorted(profile.domains)),
                    error_code=None,
                    ready=c["ready"],
                    pending=c["pending"],
                    failed=c["failed"],
                )
            )
        for item in dynamic:
            c = sets(item.clue_type, item.profile_key, item.params_hash)
            views.append(
                ProfileView(
                    clue_type=item.clue_type,
                    profile_key=None,
                    params_hash=item.params_hash,
                    origin=ProfileOrigin.DYNAMIC,
                    status=item.status.value,
                    domains=(item.domain,),
                    error_code=item.error_code,
                    ready=c["ready"],
                    pending=c["pending"],
                    failed=c["failed"],
                )
            )
        return views
