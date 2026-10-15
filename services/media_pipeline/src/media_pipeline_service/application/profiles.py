"""Каталог профилей сервиса: статические (``asset_profiles.yaml``) ∪ динамические (ADR 0025)."""

from __future__ import annotations

import structlog

from media_pipeline_service.application.generators.base import GeneratorRegistry
from media_pipeline_service.application.ports import MediaUnitOfWork
from media_pipeline_service.domain.dynamic_profiles import DynamicProfile
from media_pipeline_service.domain.errors import InvalidProfileError
from media_pipeline_service.domain.profiles import AssetProfile, AssetProfileCatalog

log = structlog.get_logger(__name__)


class ProfileDirectory:
    """Профили, по которым строятся наборы единиц домена.

    Динамический профиль с теми же ``clue_type`` и ``params_hash``, что у статического,
    не дублирует его наборы (статический важнее). Динамические профили читаются в транзакции
    вызывающего: их немного (``GV_MEDIA__MAX_DYNAMIC_PROFILES``).
    """

    def __init__(self, static: AssetProfileCatalog, registry: GeneratorRegistry) -> None:
        self._static = static
        self._registry = registry

    @property
    def static(self) -> AssetProfileCatalog:
        """Статические профили."""
        return self._static

    @property
    def registry(self) -> GeneratorRegistry:
        """Реестр генераторов (проверка параметров динамических профилей)."""
        return self._registry

    def profile_of(self, dynamic: DynamicProfile) -> AssetProfile | None:
        """Профиль генерации по динамическому; ``None`` — параметры больше не принимаются
        генератором (например, модель ужесточили), профиль пропускается."""
        try:
            return self._registry.build_dynamic(
                clue_type=dynamic.clue_type,
                profile_key=dynamic.profile_key,
                domain=dynamic.domain,
                params=dynamic.params,
            )
        except InvalidProfileError:
            log.warning(
                "dynamic_profile.unusable",
                clue_type=dynamic.clue_type,
                params_hash=dynamic.params_hash,
            )
            return None

    async def for_domain(self, uow: MediaUnitOfWork, domain: str) -> list[AssetProfile]:
        """Статические профили домена и неотклонённые динамические."""
        profiles = self._static.for_domain(domain)
        seen = {(p.clue_type, p.params_hash) for p in profiles}
        for dynamic in await uow.profiles.list_usable(domain):
            if (dynamic.clue_type, dynamic.params_hash) in seen:
                continue
            profile = self.profile_of(dynamic)
            if profile is not None:
                profiles.append(profile)
                seen.add((profile.clue_type, profile.params_hash))
        return profiles
