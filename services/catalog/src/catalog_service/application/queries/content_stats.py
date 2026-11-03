"""Отчёт по статистике угадываний: самые лёгкие единицы (ADR 0076)."""

from __future__ import annotations

from dataclasses import dataclass

from catalog_service.application.dto import ContentDifficulty
from catalog_service.application.ports import UnitOfWorkFactory
from catalog_service.domain.content_stats import approx_median_ms
from catalog_service.domain.model import MediaDomain
from gv_core.errors import InvalidInputError


@dataclass(frozen=True, slots=True)
class ReportLimits:
    """Пределы отчёта (из настроек).

    Attributes:
        min_shown: Порог показов по умолчанию.
        limit_default: Размер выдачи по умолчанию.
        limit_max: Потолок размера выдачи.
        time_bucket_ms: Ширина корзины времени (для медианы).
    """

    min_shown: int
    limit_default: int
    limit_max: int
    time_bucket_ms: int


class EasiestContent:
    """Единицы с наибольшей долей точных ответов при показах не меньше порога."""

    def __init__(self, uow: UnitOfWorkFactory, limits: ReportLimits) -> None:
        self._uow = uow
        self._limits = limits

    async def __call__(
        self,
        *,
        mode_key: str | None = None,
        domain: MediaDomain | None = None,
        min_shown: int | None = None,
        limit: int | None = None,
    ) -> list[ContentDifficulty]:
        """Отчёт по убыванию доли точных ответов.

        Raises:
            InvalidInputError: ``limit`` больше потолка из настроек или ``min_shown`` < 1.
        """
        floor = self._limits.min_shown if min_shown is None else min_shown
        size = self._limits.limit_default if limit is None else limit
        if floor < 1 or not 1 <= size <= self._limits.limit_max:
            raise InvalidInputError(
                "некорректные min_shown или limit",
                details={"limit_max": self._limits.limit_max},
            )
        async with self._uow() as uow:
            rows = await uow.content_stats.easiest(
                min_shown=floor,
                limit=size,
                mode_key=mode_key,
                domain=domain.value if domain else None,
            )
        return [
            ContentDifficulty(
                media_item_id=r.media_item_id,
                mode_key=r.mode_key,
                title=r.title,
                domain=r.domain,
                shown=r.shown,
                exact=r.exact,
                partial=r.partial,
                miss=r.miss,
                gave_up=r.gave_up,
                exact_rate=r.exact / r.shown,
                avg_time_ms=r.time_sum_ms // r.time_count if r.time_count else None,
                median_time_ms=approx_median_ms(
                    r.time_buckets, bucket_ms=self._limits.time_bucket_ms
                ),
                exact_by_level=dict(sorted(r.level_buckets.items())),
            )
            for r in rows
        ]
